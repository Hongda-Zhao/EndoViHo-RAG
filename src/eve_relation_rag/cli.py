"""Typer command-line adapter for Milestone 2 structured queries."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from typer.core import TyperGroup

from eve_relation_rag.bootstrap import get_structured_query_application
from eve_relation_rag.planning.parser import StructuredQueryRequest
from eve_relation_rag.planning.query_plans import PageSpec
from eve_relation_rag.retrieval.structured.rendering import (
    render_structured_result_table,
    serialize_structured_response,
)
from eve_relation_rag.retrieval.structured.results import ErrorResponse, QuerySuccess
from eve_relation_rag.transport import (
    cli_exit_code_for,
    internal_error_response,
    request_validation_response,
)


class OutputFormat(StrEnum):
    """Approved structured query presentation formats."""

    json = "json"
    table = "table"


def _click_validation_response(exc: typer.BadParameter) -> ErrorResponse:
    """Convert Click's pre-callback refusals to the same stable JSON envelope."""

    parameter = getattr(exc, "param", None)
    field = getattr(parameter, "name", None) or "request"
    location = ("page", field) if field in {"limit", "cursor"} else (field,)
    validation_type = "missing" if exc.__class__.__name__ == "MissingParameter" else "invalid"
    message = (
        "A required command-line option is missing."
        if validation_type == "missing"
        else "The command-line option is invalid."
    )
    return request_validation_response(
        ({"loc": location, "type": validation_type, "msg": message},)
    )


class _StructuredTyperGroup(TyperGroup):
    """Keep Click parsing errors inside the structured-query error contract."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except typer.BadParameter as exc:
            response = _click_validation_response(exc)
            typer.echo(serialize_structured_response(response), err=True)
            if standalone_mode:
                raise SystemExit(cli_exit_code_for(response)) from None
            return cli_exit_code_for(response)

        if standalone_mode and isinstance(result, int) and result != 0:
            raise SystemExit(result)
        return result


app = typer.Typer(
    help="Auditable EndoViHo-RAG command-line interface.",
    cls=_StructuredTyperGroup,
)
structured_app = typer.Typer(
    help="Plan or execute a deterministic controlled-English structured query.",
    no_args_is_help=True,
)
app.add_typer(structured_app, name="structured")


def _request(
    *,
    release_key: str,
    question: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> StructuredQueryRequest:
    page = (
        PageSpec(limit=limit if limit is not None else 50, cursor=cursor)
        if limit is not None or cursor is not None
        else None
    )
    return StructuredQueryRequest(
        release_key=release_key,
        question=question,
        page=page,
    )


def _validation_failure(exc: ValidationError) -> None:
    response = request_validation_response(exc.errors(include_url=False))
    typer.echo(serialize_structured_response(response), err=True)
    raise typer.Exit(cli_exit_code_for(response))


def _emit_error(response: ErrorResponse) -> None:
    typer.echo(serialize_structured_response(response), err=True)
    raise typer.Exit(cli_exit_code_for(response))


@structured_app.command("plan")
def plan_command(
    release_key: Annotated[
        str,
        typer.Option("--release-key", help="Exact immutable release key."),
    ],
    question: Annotated[
        str,
        typer.Option("--question", help="Controlled-English question."),
    ],
) -> None:
    """Validate and display the server-authored query plan without fact retrieval."""

    try:
        request = _request(release_key=release_key, question=question)
        response = get_structured_query_application().plan(request)
    except ValidationError as exc:
        _validation_failure(exc)
    except Exception:  # pragma: no cover - last-resort CLI safety boundary.
        _emit_error(internal_error_response())
    if isinstance(response, ErrorResponse):
        _emit_error(response)
    typer.echo(serialize_structured_response(response))


@structured_app.command("query")
def query_command(
    release_key: Annotated[
        str,
        typer.Option("--release-key", help="Exact immutable release key."),
    ],
    question: Annotated[
        str,
        typer.Option("--question", help="Controlled-English question."),
    ],
    limit: Annotated[int | None, typer.Option("--limit", min=1, max=100)] = None,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", case_sensitive=False),
    ] = OutputFormat.json,
) -> None:
    """Execute a controlled-English query against one published release."""

    try:
        request = _request(
            release_key=release_key,
            question=question,
            limit=limit,
            cursor=cursor,
        )
        response = get_structured_query_application().query(request)
    except ValidationError as exc:
        _validation_failure(exc)
    except Exception:  # pragma: no cover - last-resort CLI safety boundary.
        _emit_error(internal_error_response())
    if isinstance(response, ErrorResponse):
        _emit_error(response)
    if output_format is OutputFormat.table and isinstance(response, QuerySuccess):
        typer.echo(render_structured_result_table(response.structured_result))
    else:
        typer.echo(serialize_structured_response(response))


if __name__ == "__main__":  # pragma: no cover
    app()
