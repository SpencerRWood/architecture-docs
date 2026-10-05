"""Private disposable PostgreSQL 16 databases; never connect to runtime secrets."""

import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


def docker(arguments: list[str]) -> str:
    executable = shutil.which("docker")
    if executable is None:
        pytest.fail("PostgreSQL integration tests require Docker")
    result = subprocess.run(  # noqa: S603 -- fixed Docker executable and test arguments
        [executable, *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        pytest.fail("Disposable PostgreSQL Docker operation failed")
    return result.stdout.strip()


@pytest.fixture(scope="session")
def postgres_admin_url() -> Iterator[str]:
    name = "architecture-docs-tests-" + uuid4().hex
    docker(
        [
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_PASSWORD=fixture-password",
            "postgres:16-alpine",
        ]
    )
    try:
        port = docker(["port", name, "5432/tcp"]).rsplit(":", 1)[-1]
        url = f"postgresql://postgres:fixture-password@127.0.0.1:{port}/postgres"
        for _ in range(100):
            try:
                with psycopg.connect(url, connect_timeout=1, autocommit=True) as conn:
                    conn.execute(
                        "CREATE ROLE architecture_docs LOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
                        "PASSWORD 'fixture-password'"
                    )
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        else:
            pytest.fail("Disposable PostgreSQL did not become ready")
        yield url
    finally:
        docker(["stop", "--time", "5", name])


@pytest.fixture
def database_factory(postgres_admin_url: str) -> Iterator[Callable[[], str]]:
    names = []

    def create() -> str:
        name = "test_" + uuid4().hex
        with psycopg.connect(postgres_admin_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER architecture_docs").format(
                    sql.Identifier(name)
                )
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(name)
                )
            )
        names.append(name)
        return (
            postgres_admin_url.rsplit("/", 1)[0].replace(
                "//postgres:", "//architecture_docs:"
            )
            + "/"
            + name
        )

    yield create
    with psycopg.connect(postgres_admin_url, autocommit=True) as conn:
        for name in names:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def database_url(database_factory: Callable[[], str]) -> str:
    return database_factory()
