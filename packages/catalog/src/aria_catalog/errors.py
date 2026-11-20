"""Whose fault a failed search is.

The agent's search tool shows a `CatalogQueryError` to the model, which can correct its call.
Anything else, `CatalogBackendError` included, is logged and reported as a generic failure.
"""


class CatalogQueryError(ValueError):
    """The search arguments are invalid. The message tells the model how to correct the call."""


class CatalogBackendError(RuntimeError):
    """The catalog backend, its response or this deployment's catalog settings are at fault.

    The message is a fixed description of the fault and is logged: never put argument values,
    response content, URLs or credentials in it.
    """
