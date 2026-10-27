-- Status table that replaces the "Status" tab of the Google Sheet.
-- Same columns, same behavior: one row per Student ID, the latest run wins.
-- Run once on SiteGround (phpMyAdmin → SQL) before deploying status_db.py.

CREATE TABLE IF NOT EXISTS byw_reportgen_status (
  -- _bin: the sheet matched IDs as exact strings ("A01" != "a01"); keep that.
  student_id          VARCHAR(191)  COLLATE utf8mb4_bin NOT NULL,   -- B  Student ID
  processed_at        DATETIME      NOT NULL,   -- A  Timestamp (UTC)
  nombre              VARCHAR(255)  NULL,       -- C  Nombre
  email               VARCHAR(191)  NULL,       -- D  Email
  rol                 VARCHAR(64)   NULL,       -- E  Rol
  status              VARCHAR(16)   NOT NULL,   -- F  ok | error | resent | recomputed
  link_estudiante     VARCHAR(1024) NULL,       -- G  Link Estudiante
  link_padres         VARCHAR(1024) NULL,       -- H  Link Padres
  link_ccr            VARCHAR(1024) NULL,       -- I  Link CCR
  link_input_json     VARCHAR(1024) NULL,       -- J  Link Input JSON
  email_enviado       VARCHAR(32)   NULL,       -- K  Email enviado?
  drive_upload        VARCHAR(32)   NULL,       -- L  Drive upload?
  siteground_upload   VARCHAR(32)   NULL,       -- M  SiteGround upload?
  error               MEDIUMTEXT    NULL,       -- N  Error
  legacy_links_json   MEDIUMTEXT    NULL,       -- O  Legacy Links (JSON)
  PRIMARY KEY (student_id),
  KEY idx_email (email),
  KEY idx_nombre (nombre),
  KEY idx_processed_at (processed_at),
  KEY idx_status_rol (status, rol, processed_at),
  KEY idx_flags (email_enviado, drive_upload, siteground_upload)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
