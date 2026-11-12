from pydantic import BaseModel


class WidgetQuery(BaseModel):
    kind: str = "WidgetQuery"
