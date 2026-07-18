from __future__ import annotations

import sqlite3


def mark_running_jobs_interrupted(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        UPDATE jobs
        SET status = 'interrupted',
            current_message = 'Приложение было перезапущено'
        WHERE status = 'running'
        """
    )
    return cursor.rowcount
