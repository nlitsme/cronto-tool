"""Expected image-sampling failures; programming errors are not suppressed."""


class SamplingError(ValueError):
    """An image candidate cannot provide a usable module lattice or transform."""
