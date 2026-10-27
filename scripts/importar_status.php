<?php
/**
 * importar_status.php
 * -------------------
 * One-off: copies the "Status" tab of the status Google Sheet into
 * byw_reportgen_status. Runs on SiteGround over SSH (PuTTY), using the DB
 * creds from config/config.php. No Google access needed: it reads a file.
 *
 * 1. Google Sheet → File → Download → Microsoft Excel (.xlsx)
 *    (or "Comma-separated values" with the Status tab open)
 * 2. Upload it next to this script
 * 3. php importar_status.php status.xlsx                      # check only, writes nothing
 *    php importar_status.php status.xlsx --apply              # check + import
 *    php importar_status.php status.xlsx --apply --skip-bad   # import, leaving out rows that fail checks
 *
 * Checks: header matches, every row has a Student ID and a readable Timestamp,
 * values fit their column, Legacy Links is valid JSON, Status is a known value,
 * and Student IDs repeated across rows.
 *
 * Repeated Student IDs: the table keeps one row per student (like the sheet's
 * writer does), so only the newest one is imported. The older ones go to
 * status_import_duplicates.csv so no record is lost. Rows left out by
 * --skip-bad go to status_import_rejects.csv.
 *
 * The import never overwrites a newer row: if a student already has a row in
 * the table (from reportgen's dual-write flush) with a later processed_at,
 * that one is kept.
 *
 * Table DDL: sql/byw_reportgen_status.sql (run it first, e.g. in phpMyAdmin)
 */

ini_set('display_errors', '1');
ini_set('memory_limit', '1024M');
error_reporting(E_ALL);
set_time_limit(0);

if (php_sapi_name() !== 'cli') {
    http_response_code(403);
    exit("CLI only\n");
}

// ---------- Columns (same order as the sheet, A..O) ----------
const HEADERS = [
    'Timestamp', 'Student ID', 'Nombre', 'Email', 'Rol', 'Status',
    'Link Estudiante', 'Link Padres', 'Link CCR', 'Link Input JSON',
    'Email enviado?', 'Drive upload?', 'SiteGround upload?', 'Error', 'Legacy Links (JSON)',
];
const DB_COLUMNS = [
    'processed_at', 'student_id', 'nombre', 'email', 'rol', 'status',
    'link_estudiante', 'link_padres', 'link_ccr', 'link_input_json',
    'email_enviado', 'drive_upload', 'siteground_upload', 'error', 'legacy_links_json',
];
// VARCHAR limits from the DDL; 0 = MEDIUMTEXT (no practical limit)
const MAX_LEN = [0, 191, 255, 191, 64, 16, 1024, 1024, 1024, 1024, 32, 32, 32, 0, 0];
const KNOWN_STATUSES = ['ok', 'error', 'resent', 'recomputed'];
const TS_FORMATS = ['Y-m-d H:i:s \U\T\C', 'Y-m-d H:i:s', 'd/m/Y H:i:s', 'm/d/Y H:i:s', 'Y-m-d', 'd/m/Y'];
const CHUNK = 500;

// ---------- Args ----------
$args    = array_slice($argv, 1);
$apply   = in_array('--apply', $args, true);
$skipBad = in_array('--skip-bad', $args, true);
$files   = array_values(array_filter($args, fn($a) => substr($a, 0, 2) !== '--'));
if (count($files) !== 1) {
    exit("Usage: php importar_status.php <status.xlsx|status.csv> [--apply] [--skip-bad]\n");
}
$inputPath = $files[0];
if (!is_file($inputPath)) exit("❌ File not found: {$inputPath}\n");

$dupesCsv   = __DIR__ . '/status_import_duplicates.csv';
$rejectsCsv = __DIR__ . '/status_import_rejects.csv';

// ---------- Readers ----------
/** Returns [[sheetRowNumber, [cells...]], ...] including the header row. */
function read_csv_file(string $path): array {
    $fh = fopen($path, 'r');
    $rows = [];
    $n = 0;
    while (($row = fgetcsv($fh, 0, ',', '"', '')) !== false) {
        $n++;
        if ($n === 1 && isset($row[0])) $row[0] = preg_replace('/^\xEF\xBB\xBF/', '', $row[0]);
        $rows[] = [$n, $row];
    }
    fclose($fh);
    return $rows;
}

function col_index(string $cellRef): int {
    $letters = preg_replace('/\d+/', '', $cellRef);
    $i = 0;
    foreach (str_split($letters) as $ch) $i = $i * 26 + (ord($ch) - 64);
    return $i - 1;
}

/** Reads the "Status" sheet of an .xlsx (or the first sheet if there is no "Status"). */
function read_xlsx_file(string $path): array {
    $zip = new ZipArchive();
    if ($zip->open($path) !== true) exit("❌ Could not open {$path} as .xlsx\n");

    $relNs = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    $wb    = simplexml_load_string($zip->getFromName('xl/workbook.xml'));
    $rels  = simplexml_load_string($zip->getFromName('xl/_rels/workbook.xml.rels'));

    $targets = [];
    foreach ($rels->Relationship as $rel) $targets[(string)$rel['Id']] = (string)$rel['Target'];

    $sheetTarget = null;
    foreach ($wb->sheets->sheet as $sheet) {
        $rid = (string)$sheet->attributes($relNs)['id'];
        if ($sheetTarget === null || (string)$sheet['name'] === 'Status') {
            $sheetTarget = $targets[$rid];
            if ((string)$sheet['name'] === 'Status') break;
        }
    }
    $sheetTarget = ltrim(preg_replace('#^/?xl/#', '', $sheetTarget), '/');

    $shared = [];
    $ssXml  = $zip->getFromName('xl/sharedStrings.xml');
    if ($ssXml !== false) {
        foreach (simplexml_load_string($ssXml)->si as $si) {
            $text = '';
            if (isset($si->t)) $text = (string)$si->t;
            foreach ($si->r as $run) $text .= (string)$run->t;
            $shared[] = $text;
        }
    }

    $sheetXml = simplexml_load_string($zip->getFromName('xl/' . $sheetTarget));
    $zip->close();

    $rows = [];
    foreach ($sheetXml->sheetData->row as $row) {
        $cells = [];
        foreach ($row->c as $c) {
            $type = (string)$c['t'];
            if ($type === 's')             $val = $shared[(int)$c->v] ?? '';
            elseif ($type === 'inlineStr') $val = (string)$c->is->t;
            elseif ($type === 'b')         $val = ((string)$c->v === '1') ? 'TRUE' : 'FALSE';
            else                           $val = (string)$c->v;
            $cells[col_index((string)$c['r'])] = $val;
        }
        $width = $cells ? max(array_keys($cells)) + 1 : 0;
        $dense = [];
        for ($i = 0; $i < $width; $i++) $dense[] = $cells[$i] ?? '';
        $rows[] = [(int)$row['r'], $dense];
    }
    return $rows;
}

// ---------- Cell helpers ----------
/** Numbers that are whole (e.g. "12345", "1.2345E+11") → plain integer string. */
function clean_cell($v): string {
    $v = trim((string)$v);
    if ($v !== '' && is_numeric($v) && preg_match('/[.eE]/', $v)) {
        $f = (float)$v;
        if (floor($f) === $f && abs($f) < 1e15) return sprintf('%.0f', $f);
    }
    return $v;
}

/** Returns 'Y-m-d H:i:s' or null. Accepts the writer's text format, common date formats and Excel serials. */
function parse_ts(string $s): ?string {
    if ($s === '') return null;
    if (is_numeric($s)) {  // the sheet turned it into a real date: Excel serial days since 1899-12-30
        $unix = (int)round(((float)$s - 25569) * 86400);
        return gmdate('Y-m-d H:i:s', $unix);
    }
    foreach (TS_FORMATS as $fmt) {
        $dt = DateTime::createFromFormat('!' . $fmt, $s, new DateTimeZone('UTC'));
        $err = DateTime::getLastErrors();
        if ($dt !== false && (!$err || ($err['warning_count'] === 0 && $err['error_count'] === 0))) {
            return $dt->format('Y-m-d H:i:s');
        }
    }
    return null;
}

function write_csv(string $path, array $header, array $rows): void {
    $fh = fopen($path, 'w');
    fwrite($fh, "\xEF\xBB\xBF");  // BOM so Excel opens accents correctly
    fputcsv($fh, $header, ',', '"', '');
    foreach ($rows as $r) fputcsv($fh, $r, ',', '"', '');
    fclose($fh);
}

// ---------- Read ----------
echo "📥 Reading {$inputPath}...\n";
$ext  = strtolower(pathinfo($inputPath, PATHINFO_EXTENSION));
$rows = $ext === 'csv' ? read_csv_file($inputPath) : read_xlsx_file($inputPath);
if (count($rows) < 2) exit("File is empty, nothing to import\n");

[, $headerCells] = array_shift($rows);
$header = array_map('clean_cell', $headerCells);
if (array_slice($header, 0, count(HEADERS)) !== HEADERS) {
    echo "❌ Header does not match the expected columns:\n";
    foreach (HEADERS as $i => $want) {
        $got  = $header[$i] ?? '';
        $mark = $want === $got ? '  ' : '❌';
        echo "   {$mark} " . chr(65 + $i) . ": expected '{$want}', found '{$got}'\n";
    }
    exit(1);
}

// ---------- Check ----------
$total = count(HEADERS);
$good  = [];  // [sheetRow, params]
$bad   = [];  // [sheetRow, cells, problems]
foreach ($rows as [$n, $raw]) {
    $cells = array_map('clean_cell', $raw);
    $extra = array_filter(array_slice($cells, $total), fn($v) => $v !== '');
    $cells = array_pad(array_slice($cells, 0, $total), $total, '');
    if (implode('', $cells) === '') continue;  // fully blank line

    $problems = [];
    $blocking = false;
    $ts = parse_ts($cells[0]);
    if ($ts === null)       { $problems[] = "unreadable Timestamp '{$cells[0]}'"; $blocking = true; }
    if ($cells[1] === '')   { $problems[] = 'blank Student ID'; $blocking = true; }
    if ($cells[14] !== '') {
        json_decode($cells[14]);
        if (json_last_error() !== JSON_ERROR_NONE) { $problems[] = 'Legacy Links is not valid JSON'; $blocking = true; }
    }
    if ($extra)             { $problems[] = 'has values past column O'; $blocking = true; }
    foreach (MAX_LEN as $i => $limit) {
        $len = mb_strlen($cells[$i]);
        if ($limit && $len > $limit) { $problems[] = DB_COLUMNS[$i] . " is {$len} chars (max {$limit})"; $blocking = true; }
    }
    // Unknown status alone is only a warning: the row still fits the table.
    if (!in_array($cells[5], KNOWN_STATUSES, true)) $problems[] = "unknown Status '{$cells[5]}'";

    if ($blocking) { $bad[] = [$n, $cells, $problems]; continue; }
    if ($problems) echo "  ⚠️  row {$n}: " . implode('; ', $problems) . "\n";

    $params = array_map(fn($v) => $v === '' ? null : $v, $cells);
    $params[0] = $ts;
    $params[5] = $cells[5];  // NOT NULL in the table
    $good[] = [$n, $params];
}

// ---------- Duplicates: newest per Student ID wins (ties → lowest in the sheet) ----------
$byId = [];
foreach ($good as $entry) $byId[$entry[1][1]][] = $entry;
$keep  = [];
$dupes = [];
foreach ($byId as $entries) {
    usort($entries, fn($a, $b) => [$a[1][0], $a[0]] <=> [$b[1][0], $b[0]]);
    $keep[] = array_pop($entries);
    foreach ($entries as $e) $dupes[] = $e;
}

$statusCounts = array_count_values(array_map(fn($e) => (string)$e[1][5], $keep));
echo "\n" . str_repeat('=', 60) . "\n";
echo "Rows in file (excluding header):   " . count($rows) . "\n";
echo "Rows passing checks:               " . count($good) . "\n";
echo "Rows failing checks:               " . count($bad) . "\n";
echo "Distinct Student IDs to import:    " . count($keep) . "\n";
echo "Older repeats of a Student ID:     " . count($dupes) . "\n";
echo "Status values:                     " . json_encode($statusCounts, JSON_UNESCAPED_UNICODE) . "\n";
echo str_repeat('=', 60) . "\n";

if ($bad) {
    echo "\nRows failing checks (first 50):\n";
    foreach (array_slice($bad, 0, 50) as [$n, , $p]) echo "  row {$n}: " . implode('; ', $p) . "\n";
    write_csv($rejectsCsv, array_merge(['sheet_row'], HEADERS, ['problems']),
        array_map(fn($b) => array_merge([$b[0]], $b[1], [implode('; ', $b[2])]), $bad));
    echo "  → all of them written to {$rejectsCsv}\n";
}

if ($dupes) {
    $repeated = array_count_values(array_map(fn($d) => $d[1][1], $dupes));
    arsort($repeated);
    echo "\n" . count($repeated) . " Student IDs appear more than once (top 20):\n";
    foreach (array_slice($repeated, 0, 20, true) as $sid => $extra) echo "  '{$sid}': " . ($extra + 1) . " rows\n";
    usort($dupes, fn($a, $b) => $a[0] <=> $b[0]);
    write_csv($dupesCsv, array_merge(['sheet_row'], HEADERS),
        array_map(fn($d) => array_merge([$d[0]], array_map(fn($v) => (string)$v, $d[1])), $dupes));
    echo "  → older rows written to {$dupesCsv} (only the newest is imported)\n";
}

if (!$apply) exit("\nDry run: nothing written. Re-run with --apply to import.\n");
if ($bad && !$skipBad) {
    echo "\n❌ Not importing: some rows fail checks. Fix them in the sheet, or pass --skip-bad.\n";
    exit(1);
}

// ---------- Import ----------
$config   = require '/home/u5-ekeoaos2hhsd/www/staging2.geroeducacion.com/config/config.php';
$dbConfig = $config['db'];
try {
    $pdo = new PDO(
        "mysql:host={$dbConfig['host']};dbname={$dbConfig['dbname']};charset=utf8mb4",
        $dbConfig['username'],
        $dbConfig['password'],
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (Throwable $e) {
    exit("❌ DB connection error: " . $e->getMessage() . "\n");
}

// A row only replaces one that is not newer. processed_at must be assigned
// LAST: MySQL applies the assignments in order.
$guarded = array_merge(array_values(array_diff(DB_COLUMNS, ['student_id', 'processed_at'])), ['processed_at']);
$update  = implode(', ', array_map(
    fn($c) => "{$c} = IF(VALUES(processed_at) >= processed_at, VALUES({$c}), {$c})", $guarded
));
$cols        = implode(', ', DB_COLUMNS);
$placeholder = '(' . implode(', ', array_fill(0, $total, '?')) . ')';

$params = array_map(fn($k) => $k[1], $keep);
$count  = count($params);
echo "\n📤 Importing {$count} rows in chunks of " . CHUNK . "...\n";
foreach (array_chunk($params, CHUNK) as $i => $chunk) {
    $sql = "INSERT INTO byw_reportgen_status ({$cols}) VALUES "
         . implode(', ', array_fill(0, count($chunk), $placeholder))
         . " ON DUPLICATE KEY UPDATE {$update}";
    $pdo->beginTransaction();  // one transaction per chunk: a failure keeps earlier chunks
    try {
        $pdo->prepare($sql)->execute(array_merge(...$chunk));
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        exit("❌ Chunk " . ($i + 1) . " failed (earlier chunks are saved, re-running is safe): " . $e->getMessage() . "\n");
    }
    echo "   " . min(($i + 1) * CHUNK, $count) . "/{$count}\n";
}

// ---------- Verify ----------
$tableTotal = (int)$pdo->query("SELECT COUNT(*) FROM byw_reportgen_status")->fetchColumn();
$missing = 0;
foreach (array_chunk(array_map(fn($p) => $p[1], $params), 1000) as $ids) {
    $stmt = $pdo->prepare("SELECT COUNT(*) FROM byw_reportgen_status WHERE student_id IN ("
        . implode(', ', array_fill(0, count($ids), '?')) . ")");
    $stmt->execute($ids);
    $missing += count($ids) - (int)$stmt->fetchColumn();
}

echo "\n✅ Done. Table now has {$tableTotal} rows; imported IDs missing from it: {$missing}\n";
exit($missing === 0 ? 0 : 1);
