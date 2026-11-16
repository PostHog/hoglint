from rest_framework.exceptions import APIException


class EntryRefused(APIException):
    status_code = 402
