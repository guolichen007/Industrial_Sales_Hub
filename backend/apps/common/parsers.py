from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser


class ObjectJSONParser(JSONParser):
    """所有接口的 JSON 请求体必须是对象；数组/标量直接 400，避免 .get() 导致 500。"""

    def parse(self, stream, media_type=None, parser_context=None):
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError("请求体必须是 JSON 对象")
        return data
