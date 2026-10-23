"""Results of plugin entries: Ok(value) or Err(SdkError). A plugin entry returns one of them; it never raises for an expected failure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E")


class SdkError(RuntimeError):
    """An expected failure, with a message the owner can read. `code` and `details` are optional."""

    def __init__(self, message: str = "", *, code: str | None = None, details: object | None = None, **context: object):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details
        self.context = {k: v for k, v in context.items() if v is not None}

    def __str__(self) -> str:
        return self.message


class TransportError(SdkError):
    """A call between plugins, or to a service, could not be made."""


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, _default: Any) -> T:
        return self.value

    def map(self, fn: Callable[[T], Any]) -> "Ok[Any]":
        return Ok(fn(self.value))


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    error: E

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> Any:
        if isinstance(self.error, BaseException):
            raise self.error
        raise SdkError(str(self.error))

    def unwrap_or(self, default: Any) -> Any:
        return default

    def map(self, _fn: Callable[[Any], Any]) -> "Err[E]":
        return self


Result = Ok[T] | Err[E]
