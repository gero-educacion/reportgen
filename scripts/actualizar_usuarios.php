<?php

ini_set('display_errors', 1);
ini_set('display_startup_errors', 1);
error_reporting(E_ALL);

echo "SCRIPT START\n";
echo __DIR__ . "\n";
echo "the directory is: " . dirname(__DIR__) . "\n";


require_once __DIR__ . '/vendor/autoload.php';

$config = require '/home/u5-ekeoaos2hhsd/www/staging2.geroeducacion.com/config/config.php';

$dbConfig = $config['db'];
$db_pass = $dbConfig['password'];
$db_user = $dbConfig['username'];

/**
 * ✅ CONFIG
 */
define('SERVICE_ACCOUNT_FILE', $config['google']['service_account']);
define('SPREADSHEET_ID', '1yVymG89U8J0EBbvPcYMEtxuI3zHcdPggRLD1U7iCOAc');
define('SHEET_NAME', 'query_results');

/**
 * ✅ HELPERS
 */
function colLetter(int $colIndex1Based): string {
    $col = '';
    while ($colIndex1Based > 0) {
        $mod = ($colIndex1Based - 1) % 26;
        $col = chr(65 + $mod) . $col;
        $colIndex1Based = intdiv($colIndex1Based - 1, 26);
    }
    return $col;
}

echo "I made it here \n";

/**
 * ✅ SQL
 * NOTE: Your query works. Kept as-is with minimal changes.
 */
$query = <<<SQL
SELECT
          base.user_id,
          base.user_email,
          base.display_name,
          base.Cliente,
          base.Cliente_Desc,
          base.Tipo_Name,
          base.Tipo_Desc,
          base.rol,
          COALESCE(base.form_ccr,'')  AS form_ccr,
          COALESCE(base.form_197,'')  AS form_197,
          COALESCE(base.form_198,'')  AS form_198,
          COALESCE(base.form_199,'')  AS form_199,
          COALESCE(base.form_201,'')  AS form_201,
          base.reporte_ccr,
          base.autoconocimiento,
          base.autoconocimiento_padres,
          base.estado,
          COALESCE(ac.carrera_1,'') AS carrera_1,
          COALESCE(ac.carrera_2,'') AS carrera_2,
          COALESCE(ac.carrera_3,'') AS carrera_3,
          COALESCE(ac.carrera_4,'') AS carrera_4
      FROM (
          SELECT
              u.ID AS user_id,
              u.display_name,
              t.user_email,
              COALESCE(meta_data.Cliente, '') AS Cliente,
              COALESCE(cliente_desc.name, '') AS Cliente_Desc,
              COALESCE(tipo_cliente.name, '') AS Tipo_Name,
              COALESCE(tipo_desc.description, '') AS Tipo_Desc,
              COALESCE(
                SUBSTRING(um.meta_value, LOCATE('"', um.meta_value, 1) + 1,
                  LOCATE('"', um.meta_value, LOCATE('"', um.meta_value, 1) + 1)
                  - LOCATE('"', um.meta_value, 1) - 1), ''
              ) AS rol,
              MAX(CASE WHEN t.id_form = 65  THEN t.created_at END) AS form_ccr,
              MAX(CASE WHEN t.id_form = 197 THEN t.created_at END) AS form_197,
              MAX(CASE WHEN t.id_form = 198 THEN t.created_at END) AS form_198,
              MAX(CASE WHEN t.id_form = 199 THEN t.created_at END) AS form_199,
              MAX(CASE WHEN t.id_form = 201 THEN t.created_at END) AS form_201,
              COALESCE(r.reporte_ccr, '') AS reporte_ccr,
              COALESCE(r.autoconocimiento, '') AS autoconocimiento,
              COALESCE(r.autoconocimiento_padres, '') AS autoconocimiento_padres,
              CASE
                WHEN COALESCE(SUBSTRING(um.meta_value, LOCATE('"', um.meta_value, 1) + 1,
                  LOCATE('"', um.meta_value, LOCATE('"', um.meta_value, 1) + 1)
                  - LOCATE('"', um.meta_value, 1) - 1), '') IN ('counseling', 'compass')
                THEN CASE
                  WHEN MAX(CASE WHEN t.id_form = 197 THEN t.created_at END) IS NOT NULL
                    AND MAX(CASE WHEN t.id_form = 199 THEN t.created_at END) IS NOT NULL
                  THEN 'Finalizado' ELSE 'Pendiente' END
                ELSE CASE
                  WHEN MAX(CASE WHEN t.id_form = 197 THEN t.created_at END) IS NOT NULL
                    AND MAX(CASE WHEN t.id_form = 198 THEN t.created_at END) IS NOT NULL
                    AND MAX(CASE WHEN t.id_form = 199 THEN t.created_at END) IS NOT NULL
                    AND MAX(CASE WHEN t.id_form = 201 THEN t.created_at END) IS NOT NULL
                  THEN 'Finalizado' ELSE 'Pendiente' END
              END AS estado
          FROM byw_typf_items t
          LEFT JOIN byw_users u
            ON t.user_email COLLATE utf8mb4_general_ci = u.user_email COLLATE utf8mb4_general_ci
          LEFT JOIN byw_usermeta um ON um.user_id = u.ID AND um.meta_key = 'byw_capabilities'
          LEFT JOIN (
              SELECT user_id,
                MAX(CASE WHEN meta_key = 'nombre_cliente' THEN meta_value END) AS Cliente,
                MAX(CASE WHEN meta_key = 'tipo_cliente_user' THEN meta_value END) AS Tipo
              FROM byw_usermeta
              WHERE meta_key IN ('nombre_cliente','tipo_cliente_user')
              GROUP BY user_id
          ) meta_data ON meta_data.user_id = u.ID
          LEFT JOIN byw_terms        cliente_desc ON meta_data.Cliente = cliente_desc.term_id
          LEFT JOIN byw_terms        tipo_cliente ON meta_data.Tipo    = tipo_cliente.term_id
          LEFT JOIN byw_term_taxonomy tipo_desc   ON meta_data.Tipo    = tipo_desc.term_taxonomy_id
          LEFT JOIN (
              SELECT email.meta_value AS the_email,
                COALESCE(MAX(CASE WHEN tipo.meta_value = '¿Cuán Preparado/a Estás?'
                  THEN url.meta_value END), '') AS reporte_ccr,
                COALESCE(MAX(CASE WHEN tipo.meta_value IN (
                  'Reporte "Autoconocimiento"','Tu guía de autoconocimiento')
                  THEN url.meta_value END), '') AS autoconocimiento,
                COALESCE(MAX(CASE WHEN tipo.meta_value = 'Reporte "Autoconocimiento versión padres"'
                  THEN url.meta_value END), '') AS autoconocimiento_padres
              FROM byw_postmeta email
              JOIN byw_postmeta tipo ON tipo.post_id = email.post_id
              JOIN byw_postmeta url  ON url.post_id  = email.post_id
              WHERE email.meta_key = 'the_email'
                AND tipo.meta_key  = 'tipo_reporte'
                AND url.meta_key   = 'direct_url'
                AND tipo.meta_value IN (
                  '¿Cuán Preparado/a Estás?',
                  'Reporte "Autoconocimiento"',
                  'Tu guía de autoconocimiento',
                  'Reporte "Autoconocimiento versión padres"'
                )
              GROUP BY email.meta_value
          ) r ON t.user_email COLLATE utf8mb4_general_ci = r.the_email COLLATE utf8mb4_general_ci
          WHERE t.id_form IN (65,197,198,199,201)
            AND u.ID IN (
              SELECT user_id FROM byw_usermeta
              WHERE meta_key = 'byw_capabilities'
                AND (meta_value LIKE '%gs_actividades%' OR meta_value LIKE '%counseling%')
            )
          GROUP BY u.ID, u.display_name, t.user_email,
            meta_data.Cliente, cliente_desc.name, tipo_cliente.name,
            tipo_desc.description, um.meta_value,
            r.reporte_ccr, r.autoconocimiento, r.autoconocimiento_padres
      ) base
      LEFT JOIN byw_autoconocimiento ac ON base.user_email = ac.user_email
      ORDER BY base.user_email
      LIMIT 30000   
SQL;

/**
 * ✅ FETCH MYSQL
 */

echo "trying...";
try {
    $pdo = new PDO(
        "mysql:host={$dbConfig['host']};dbname={$dbConfig['dbname']};charset={$dbConfig['charset']}",
        $db_user,
        $db_pass,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::MYSQL_ATTR_INIT_COMMAND => "SET NAMES utf8mb4"
        ]
    );

    $stmt = $pdo->query($query);
    $results = $stmt->fetchAll(PDO::FETCH_ASSOC);

    if (!$results) die("No data found in database\n");

    echo "✓ Fetched " . count($results) . " rows from MySQL\n";
} catch (PDOException $e) {
    die("Database error: " . $e->getMessage() . "\n");
}

/**
 * ✅ PREPARE VALUES (header + rows)
 */
$headers = array_keys($results[0]);
$values = [$headers];

foreach ($results as $row) {
    // Defensive: ensure the order matches $headers
    $values[] = array_map(fn($h) => $row[$h] ?? '', $headers);
}

$rows = count($values);
$cols = count($headers);
$endCol = colLetter($cols);
$writeRange = SHEET_NAME . "!A1:{$endCol}{$rows}";

echo "✓ Prepared {$rows} rows x {$cols} cols\n";
echo "✓ Range: {$writeRange}\n";

/**
 * ✅ AUTH GOOGLE
 */
try {
    if (!file_exists(SERVICE_ACCOUNT_FILE)) {
        die("Service account file not found: " . SERVICE_ACCOUNT_FILE . "\n");
    }

    $client = new Google_Client();
    $client->setAuthConfig(SERVICE_ACCOUNT_FILE);
    $client->addScope(Google_Service_Sheets::SPREADSHEETS);

    $service = new Google_Service_Sheets($client);
    echo "✓ Authenticated with Google Sheets API\n";
} catch (Exception $e) {
    die("Google API auth error: " . $e->getMessage() . "\n");
}

/**
 * ✅ WRITE (clear only what you will overwrite, then batchUpdate)
 */
try {
    // Clear the exact range you're about to write (faster than A1:Z)
    $clearReq = new Google_Service_Sheets_ClearValuesRequest();
    $service->spreadsheets_values->clear(SPREADSHEET_ID, $writeRange, $clearReq);
    echo "✓ Cleared range\n";
    // After clearing, write in chunks
    $chunkSize = 1000;
    $chunks = array_chunk($values, $chunkSize);
    $totalUpdated = 0;

    foreach ($chunks as $i => $chunk) {
        $startRow = ($i * $chunkSize) + 1;
        $endRow = $startRow + count($chunk) - 1;
        $chunkRange = SHEET_NAME . "!A{$startRow}:{$endCol}{$endRow}";

        $data = [
            new Google_Service_Sheets_ValueRange([
                'range'  => $chunkRange,
                'values' => $chunk
            ])
        ];

        $batchReq = new Google_Service_Sheets_BatchUpdateValuesRequest([
            'valueInputOption' => 'RAW',
            'data' => $data
        ]);

        $res = $service->spreadsheets_values->batchUpdate(SPREADSHEET_ID, $batchReq);
        $totalUpdated += $res->getTotalUpdatedRows();
        echo "✓ Chunk " . ($i+1) . "/" . count($chunks) . " written ({$totalUpdated} rows total)\n";
    }

    echo "✓ Export complete! {$totalUpdated} total rows written\n";
} catch (Exception $e) {
    die("Google Sheets write error: " . $e->getMessage() . "\n");
}
