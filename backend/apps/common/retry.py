from django.db import IntegrityError, transaction


def create_with_code(make_code, create, attempts=5, serializer=None):
    """编号生成后插入；若并发导致编号冲突则重试（编号字段有唯一约束兜底）。"""
    last = None
    for _ in range(attempts):
        try:
            if serializer is not None:
                serializer.instance = None  # 上次失败的实例不可复用，否则会变成 update
            with transaction.atomic():
                return create(make_code())
        except IntegrityError as e:
            last = e
    raise last
