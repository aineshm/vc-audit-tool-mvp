# engine.py — diff patch
# Apply this to src/vc_audit_tool/engine.py
#
# Change 1: Add the new import (after existing methodology imports)
# ---------------------------------------------------------------
# FROM:
#     from vc_audit_tool.methodologies.berkus import BerkusMethodology
#     from vc_audit_tool.methodologies.comps import ComparableCompaniesMethodology
#     from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
#     from vc_audit_tool.methodologies.multiple_ratchet import LastRoundMultipleRatchetMethodology
#     from vc_audit_tool.methodologies.scorecard import ScorecardMethodology
#
# TO:
#     from vc_audit_tool.methodologies.berkus import BerkusMethodology
#     from vc_audit_tool.methodologies.comps import ComparableCompaniesMethodology
#     from vc_audit_tool.methodologies.direct_valuation import DirectValuationMethodology  # NEW
#     from vc_audit_tool.methodologies.last_round import LastRoundMarketAdjustedMethodology
#     from vc_audit_tool.methodologies.multiple_ratchet import LastRoundMultipleRatchetMethodology
#     from vc_audit_tool.methodologies.scorecard import ScorecardMethodology
#
#
# Change 2: Register the methodology in ValuationEngine.__init__
# ---------------------------------------------------------------
# FROM:
#     self._methodologies: dict[str, ValuationMethodology] = {
#         LastRoundMarketAdjustedMethodology.name: LastRoundMarketAdjustedMethodology(),
#         ComparableCompaniesMethodology.name: ComparableCompaniesMethodology(),
#         LastRoundMultipleRatchetMethodology.name: LastRoundMultipleRatchetMethodology(),
#         ScorecardMethodology.name: ScorecardMethodology(),
#         BerkusMethodology.name: BerkusMethodology(),
#     }
#
# TO:
#     self._methodologies: dict[str, ValuationMethodology] = {
#         DirectValuationMethodology.name: DirectValuationMethodology(),      # NEW — highest priority
#         LastRoundMarketAdjustedMethodology.name: LastRoundMarketAdjustedMethodology(),
#         ComparableCompaniesMethodology.name: ComparableCompaniesMethodology(),
#         LastRoundMultipleRatchetMethodology.name: LastRoundMultipleRatchetMethodology(),
#         ScorecardMethodology.name: ScorecardMethodology(),
#         BerkusMethodology.name: BerkusMethodology(),
#     }
