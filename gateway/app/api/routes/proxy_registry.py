"""Register documented gateway proxies without tying routes to a specific upstream."""
from fastapi import APIRouter, Depends, Response
from app.api.dependencies.security import get_current_principal


def register(target: APIRouter, routes, prefix_name: str, endpoint, path_parameters, integer_parameters) -> None:
    for path, methods, public, summary, query, body in routes:
        for method in methods:
            parameters = [
                {"name": name, "in": "path", "required": True,
                 "schema": {"type": "integer"} if name in integer_parameters else {"type": "string", "format": "uuid"}}
                for name in path_parameters if "{" + name + "}" in path
            ]
            parameters += [{"name": name, "in": "query", "required": False, "schema": schema}
                           for name, schema in (query or {}).items()]
            docs = {"parameters": parameters}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                docs["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"type": "object"}, "example": body}}}
            target.add_api_route(
                path, endpoint, methods=[method], response_class=Response, summary=summary,
                dependencies=[] if public else [Depends(get_current_principal)],
                name=f"{prefix_name}_{method.lower()}_{path.strip('/').replace('/', '_') or 'root'}",
                openapi_extra=docs,
            )
