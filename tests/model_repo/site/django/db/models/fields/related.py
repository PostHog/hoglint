from django.db.models.fields import Field


class ForeignKey(Field):
    pass


class OneToOneField(ForeignKey):
    pass


class ManyToManyField(Field):
    pass
