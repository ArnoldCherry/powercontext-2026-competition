from __future__ import annotations

import re

import pymysql
from pymysql.cursors import DictCursor

from .config import Settings

TABLES = (
    "sdd_query_log",
    "sdd_import_runs",
    "sdd_spec_keywords",
    "sdd_spec_links",
    "sdd_spec_entries",
    "sdd_code_symbols",
    "sdd_test_cases",
    "sdd_documents",
)

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sdd_documents (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        path VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        imported_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_document (path(191), kind)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_spec_entries (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        doc_path VARCHAR(512) NOT NULL,
        req_id VARCHAR(64) NOT NULL,
        title VARCHAR(512) NOT NULL,
        body MEDIUMTEXT NOT NULL,
        source MEDIUMTEXT NOT NULL,
        line_start INT NOT NULL,
        line_end INT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        imported_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_req (req_id),
        KEY idx_spec_doc (doc_path(191))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_spec_keywords (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        req_id VARCHAR(64) NOT NULL,
        keyword VARCHAR(191) NOT NULL,
        doc_path VARCHAR(512) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_keyword (req_id, keyword)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_spec_links (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        req_id VARCHAR(64) NOT NULL,
        target_kind VARCHAR(16) NOT NULL,
        target_path VARCHAR(512) NOT NULL,
        target_symbol VARCHAR(255) NOT NULL,
        relation VARCHAR(32) NOT NULL,
        rationale TEXT NOT NULL,
        trace_path VARCHAR(512) NOT NULL,
        PRIMARY KEY (id),
        KEY idx_link_req (req_id),
        KEY idx_link_target (target_path(191), target_symbol)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_code_symbols (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        path VARCHAR(512) NOT NULL,
        qualname VARCHAR(255) NOT NULL,
        symbol_kind VARCHAR(32) NOT NULL,
        line_start INT NOT NULL,
        line_end INT NOT NULL,
        signature TEXT NOT NULL,
        docstring TEXT NOT NULL,
        source MEDIUMTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_symbol (path(191), qualname(191))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_test_cases (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        path VARCHAR(512) NOT NULL,
        name VARCHAR(255) NOT NULL,
        line_start INT NOT NULL,
        line_end INT NOT NULL,
        markers TEXT NOT NULL,
        docstring TEXT NOT NULL,
        source MEDIUMTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uq_test (path(191), name(191))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_import_runs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        imported_at DATETIME NOT NULL,
        manifest_sha256 CHAR(64) NOT NULL,
        documents_json MEDIUMTEXT NOT NULL,
        specs_count INT NOT NULL,
        code_count INT NOT NULL,
        tests_count INT NOT NULL,
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_query_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        task_id VARCHAR(64) NOT NULL,
        task_sha256 CHAR(64) NOT NULL,
        statements MEDIUMTEXT NOT NULL,
        params_json MEDIUMTEXT NOT NULL,
        matched_reqs TEXT NOT NULL,
        response_sha256 CHAR(64) NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        KEY idx_task (task_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)


def connect(settings: Settings, with_database: bool = True):
    if not settings.seekdb_password:
        raise RuntimeError("SEEKDB_PASSWORD is not set; copy .env.example to .env and fill it")
    kwargs = {
        "host": settings.seekdb_host,
        "port": settings.seekdb_port,
        "user": settings.seekdb_user,
        "password": settings.seekdb_password,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if with_database:
        kwargs["database"] = settings.seekdb_database
    return pymysql.connect(**kwargs)


def ensure_database(settings: Settings) -> None:
    database = settings.seekdb_database
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError(f"invalid database name: {database!r}")
    connection = connect(settings, with_database=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        connection.commit()
    finally:
        connection.close()


def ensure_schema(connection) -> None:
    with connection.cursor() as cursor:
        for statement in SCHEMA:
            cursor.execute(statement)
    connection.commit()


def reset_schema(connection) -> None:
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
    connection.commit()


def server_version(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION() AS version")
        return str(cursor.fetchone()["version"])


def list_tables(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        return [str(next(iter(row.values()))) for row in cursor.fetchall()]
