"""Offline CLI source fixture; the collector must never import or execute it."""


def deploy() -> None:
    raise RuntimeError("must not execute this source")
