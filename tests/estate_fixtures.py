"""Explicit tiny estate used by the pre-existing representative repositories."""

from architecture_docs.estate import EstateContract, ExpectedRepository, RequiredDomain

FIXTURE_ESTATE = EstateContract(
    (ExpectedRepository("fixture/service", "service", True),),
    (
        RequiredDomain(
            "runtime",
            fact_prefixes=("architecture.node", "package.name", "container.base_image"),
        ),
    ),
)
