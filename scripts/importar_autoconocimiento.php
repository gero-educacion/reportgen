<?php
file_put_contents(__DIR__.'/logs.txt', "[".date('Y-m-d H:i:s')."] Script entered\n", FILE_APPEND);
ini_set('display_errors', '1');
ini_set('display_startup_errors', '1');
error_reporting(E_ALL);
set_time_limit(300);

$LOG_FILE = __DIR__ . '/logs.txt';

// log all PHP errors to logs.txt
ini_set('log_errors', 1);
ini_set('error_log', $LOG_FILE);

// also capture shutdown (fatal) errors
register_shutdown_function(function() use ($LOG_FILE) {
    $error = error_get_last();
    if ($error !== null) {
        $msg = "[" . date('Y-m-d H:i:s') . "] ❌ Fatal error: {$error['message']} in {$error['file']} on line {$error['line']}\n";
        file_put_contents($LOG_FILE, $msg, FILE_APPEND | LOCK_EX);
    }
});

// --- Helper to log messages with timestamp ---
function log_msg($file, $msg) {
    file_put_contents($file, "[" . date('Y-m-d H:i:s') . "] " . $msg . "\n", FILE_APPEND | LOCK_EX);
}

// ---------- Safe DB config for CLI (reads wp-config.php without loading WP) ----------
$is_cli = (php_sapi_name() === 'cli');
$nl     = $is_cli ? "\n" : "<br>";
$LOG_FILE = __DIR__ . '/logs.txt';
// --- Start log ---
log_msg($LOG_FILE, "importar_autoconocimiento.php started (cli=" . ($is_cli ? 'yes' : 'no') . ")");

// ---- DB Connection ----
$config = require '/home/u5-ekeoaos2hhsd/www/staging2.geroeducacion.com/config/config.php';

$dbConfig = $config['db'];

$db_host = $dbConfig['host'];
$db_name = $dbConfig['dbname'];
$db_user = $dbConfig['username'];
$db_pass = $dbConfig['password'];

$dsn = "mysql:host=$db_host;dbname=$db_name;charset=utf8mb4";
$options = [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION];

try {
    $pdo = new PDO($dsn, $db_user, $db_pass, $options);
    log_msg($LOG_FILE, "✅ DB connection OK");
} catch (Throwable $e) {
    http_response_code(500);
    $msg = "Error de conexión: " . $e->getMessage();
    log_msg($LOG_FILE, "❌ {$msg}");
    if (!$is_cli) echo $msg . $nl;
    exit;
}

// ---- Download CSV ----
$csvUrl = 'https://docs.google.com/spreadsheets/d/1-pHNm73qCxtGdliv3-_gxdGg1F2uVyWgn6PGNveE8ig/export?format=csv&gid=0';
$ch = curl_init($csvUrl);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => true,
    CURLOPT_TIMEOUT => 60,
    CURLOPT_SSL_VERIFYPEER => true,
]);
$csvContent = curl_exec($ch);
$httpCode   = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curlErr    = curl_error($ch);
curl_close($ch);

if ($csvContent === false || $httpCode !== 200) {
    http_response_code(502);
    $msg = "❌ No se pudo descargar el CSV (HTTP {$httpCode}). Detalle: {$curlErr}";
    log_msg($LOG_FILE, $msg);
    if (!$is_cli) echo $msg . $nl;
    exit;
}
$lines = preg_split("/\r\n|\n|\r/", $csvContent);
$lines = array_values(array_filter($lines, fn($l) => $l !== null));
if (!$lines || count($lines) < 2) {
    $msg = "❌ CSV vacío o sin encabezados/filas suficientes.";
    log_msg($LOG_FILE, $msg);
    if (!$is_cli) echo $msg . $nl;
    exit;
}
$rows = count($lines);
log_msg($LOG_FILE, "✅ CSV downloaded (HTTP {$httpCode}) with {$rows} rows :) ");



// ---- Prepare headers ----
$headersRaw   = str_getcsv($lines[0]);
$headersRaw   = array_map('trim', $headersRaw);
$headersLower = array_map('mb_strtolower', $headersRaw);

$toAssoc = function(array $headersLower, array $row) {
    $assoc = [];
    foreach ($headersLower as $i => $h) {
        $assoc[$h] = $row[$i] ?? null;
    }
    return $assoc;
};

// ---- SQL ----
$sql = "
INSERT INTO byw_autoconocimiento
  (user_id, user_email, nombre, carrera_1, carrera_2, carrera_3, carrera_4)
SELECT
  u.ID,
  u.user_email,
  u.display_name,
  :c1, :c2, :c3, :c4
FROM byw_users AS u
WHERE LOWER(u.user_email) = LOWER(:email)
ON DUPLICATE KEY UPDATE
  user_email = VALUES(user_email),
  nombre     = VALUES(nombre),
  carrera_1  = VALUES(carrera_1),
  carrera_2  = VALUES(carrera_2),
  carrera_3  = VALUES(carrera_3),
  carrera_4  = VALUES(carrera_4)
";
$stm = $pdo->prepare($sql);

$imported       = 0;
$skipped        = 0;
$notFound       = [];
$errors         = [];
$importedEmails = [];

$pdo->beginTransaction();

try {
    for ($i = 1; $i < count($lines); $i++) {
        $line = $lines[$i];
        if ($line === null || trim($line) === '') { $skipped++; continue; }

        $row = str_getcsv($line);
        if (count($row) < count($headersLower)) {
            $row = array_pad($row, count($headersLower), null);
        }

        $assoc = $toAssoc($headersLower, array_map('trim', $row));

        $email =
            ($assoc['user_email'] ?? null) ??
            ($assoc['email'] ?? null) ??
            ($assoc['e-mail'] ?? null) ??
            ($assoc['e_mail'] ?? null) ??
            ($assoc['email_address'] ?? null) ??
            ($assoc['email address'] ?? null) ??
            ($assoc['mail'] ?? null);

        $email = mb_strtolower(trim((string)$email));
        if ($email === '') { $skipped++; continue; }

        $c1 = $assoc['carrera_1'] ?? null;
        $c2 = $assoc['carrera_2'] ?? null;
        $c3 = $assoc['carrera_3'] ?? null;
        $c4 = $assoc['carrera_4'] ?? null;

        try {
            $stm->execute([
                ':email' => $email,
                ':c1'    => $c1,
                ':c2'    => $c2,
                ':c3'    => $c3,
                ':c4'    => $c4,
            ]);

            $checkStmt = $pdo->prepare("SELECT ID FROM byw_users WHERE LOWER(user_email) = LOWER(:email)");
            $checkStmt->execute([':email' => $email]);
            if (!$checkStmt->fetchColumn()) {
                log_msg($LOG_FILE, "❌ No match for {$email} in byw_users");
                $notFound[] = $email;
                continue;
            }



            $imported++;
            $importedEmails[] = $email;
        } catch (Throwable $e) {
            $errors[] = "Error con {$email}: " . $e->getMessage();
        }
    }
    if (!empty($notFound)) {
    foreach ($notFound as $nf) {
        log_msg($LOG_FILE, "🔎 Not found in byw_users: {$nf}");
    }
}

if (!empty($errors)) {
    foreach ($errors as $err) {
        log_msg($LOG_FILE, "⚠️ Error: {$err}");
    }
}


    $pdo->commit();
    log_msg($LOG_FILE, "✅ Finished loop (imported={$imported}, skipped={$skipped})");
} catch (Throwable $t) {
    $pdo->rollBack();
    $msg = "❌ Error procesando CSV: " . $t->getMessage();
    log_msg($LOG_FILE, $msg);
    if (!$is_cli) echo $msg . $nl;
    exit;
}

// === JSON TRACKING ===
$jsonTotalPath = __DIR__ . '/usuarios_importados.json';
$jsonNewPath   = __DIR__ . '/nuevos_usuarios.json';

$existingEmails = [];
if (file_exists($jsonTotalPath)) {
    $existingContent = file_get_contents($jsonTotalPath);
    $existingEmails  = json_decode($existingContent, true) ?? [];
}

$newEmails = array_values(array_diff($importedEmails, $existingEmails));

file_put_contents($jsonTotalPath, json_encode(array_unique(array_merge($existingEmails, $newEmails)), JSON_PRETTY_PRINT));
file_put_contents($jsonNewPath, json_encode($newEmails, JSON_PRETTY_PRINT));

log_msg($LOG_FILE, "✅ JSON updated: nuevos=" . count($newEmails) . ", total=" . count($existingEmails) + count($newEmails));

// ---- Run Python Script ----
$python   = __DIR__ . '/venv/bin/python3';
$script   = __DIR__ . '/sender.py';
$pyOutput = shell_exec("$python $script 2>&1");


$now = date('Y-m-d H:i:s');
log_msg($LOG_FILE, "✅ importar_autoconocimiento.php finished (imported={$imported}, skipped={$skipped})");
if ($pyOutput !== null && $pyOutput !== '') {
    log_msg($LOG_FILE, "🐍 sender.py output: " . str_replace(["\r\n","\r"], "\n", $pyOutput));
}

// ---- Echo to browser/cron ----
echo "Timestamp: {$now}{$nl}";
echo "✅ Importación finalizada.$nl";
echo "🧾 Filas importadas/actualizadas: {$imported}{$nl}";
echo "↪️ Filas salteadas (vacías o sin email): {$skipped}{$nl}";
if (!empty($errors)) {
    echo "⚠️ Errores: " . count($errors) . "{$nl}" . implode($nl, $errors) . $nl;
}
if (!empty($notFound)) {
    echo "🔎 Emails no encontrados en byw_users: " . count($notFound) . $nl;
}
if ($is_cli) {
    echo rtrim($pyOutput ?? '', "\r\n") . $nl;
} else {
    echo "<pre>" . htmlspecialchars($pyOutput ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') . "</pre>";
}
