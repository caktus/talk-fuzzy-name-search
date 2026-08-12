"""Factories for records app models."""

import factory
from factory.django import DjangoModelFactory

from records.models import Person


class PersonFactory(DjangoModelFactory):
    """Factory for generating Person test records."""

    class Meta:
        model = Person
        django_get_or_create = ("first_name", "last_name", "date_of_birth")

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    middle_name = factory.Maybe(
        decider=factory.LazyAttribute(lambda x: False),
        yes_declaration=factory.Faker("first_name"),
        no_declaration=None,
    )
    date_of_birth = factory.Faker("date_of_birth", minimum_age=18, maximum_age=80)
    nicknames = factory.LazyFunction(list)
