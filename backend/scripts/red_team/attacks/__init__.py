"""Attack families. Importing this package registers every attack in ``core.REGISTRY``."""

from . import (  # noqa: F401
    fam1_arith,
    fam2_stream,
    fam3_render,
    fam4_meta,
    fam5_image,
    fam6_reuse,
    fam7_invoice,
    fam8_kyc,
    fam9_inject,
    fam10_form,
)
