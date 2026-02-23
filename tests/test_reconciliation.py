"""Tests for the reconciliation layer.

Covers: CompanyProfiler, MethodologySelector, Reconciler, ReconciliationEngine.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from vc_audit_tool.exceptions import DataSourceError
from vc_audit_tool.reconciliation.models import (
    CompanyProfile,
    ConcludedValue,
    DataPackage,
    MethodologyPlan,
    MethodologyWeight,
    ReconciledValuation,
    ReconciliationSummary,
)
from vc_audit_tool.reconciliation.profiler import CompanyProfiler
from vc_audit_tool.reconciliation.reconciler import Reconciler
from vc_audit_tool.reconciliation.selector import MethodologySelector

# ── Helpers ────────────────────────────────────────────────────────────


def _make_profile(
    stage: str = "growth",
    has_revenue: bool = True,
    estimated_arr: Decimal | None = Decimal("15000000"),
    last_round_age_months: float | None = 12.0,
    last_post_money: Decimal | None = Decimal("100000000"),
) -> CompanyProfile:
    return CompanyProfile(
        name="TestCo",
        stage=stage,  # type: ignore[arg-type]
        age_years=5.0,
        has_revenue=has_revenue,
        estimated_arr=estimated_arr,
        last_round_age_months=last_round_age_months,
        last_round_amount=Decimal("20000000"),
        last_post_money=last_post_money,
        sector="enterprise_software",
        sic_code="7372",
        headcount=150,
        government_contracts_usd=None,
        profile_summary="Growth-stage company in enterprise_software.",
        sources_used=("pitchbook", "sec_filings"),
    )


def _make_data_package(
    revenue_ltm: Decimal | None = Decimal("15000000"),
    last_post_money: Decimal | None = Decimal("100000000"),
    last_round_date: date | None = date(2025, 6, 1),
    revenue_at_last_round: Decimal | None = None,
    current_revenue: Decimal | None = None,
    regional_median: Decimal | None = None,
    scorecard_factors: dict[str, float] | None = None,
    berkus_factors: dict[str, bool | float] | None = None,
    max_pre_money: Decimal | None = None,
    peer_set_quality: str | None = None,
    target_description: str | None = None,
) -> DataPackage:
    return DataPackage(
        last_post_money=last_post_money,
        last_round_date=last_round_date,
        revenue_ltm=revenue_ltm,
        sector="enterprise_software",
        peer_set_quality=peer_set_quality,
        government_contracts_usd=None,
        as_of_date=date(2026, 2, 22),
        regional_median_pre_money=regional_median,
        scorecard_factors=scorecard_factors,
        berkus_factors=berkus_factors,
        max_pre_money_valuation=max_pre_money,
        revenue_at_last_round=revenue_at_last_round,
        current_revenue=current_revenue,
        target_description=target_description,
    )


def _fake_result(amount: float) -> dict[str, Any]:
    """Build a minimal methodology result dict matching ValuationResult.to_dict() shape."""
    return {
        "valuation_result": {
            "company_name": "TestCo",
            "methodology": "test",
            "as_of_date": "2026-02-22",
            "estimated_fair_value": {
                "amount": amount,
                "currency": "USD",
            },
            "assumptions": [],
            "inputs_used": {},
            "citations": [],
            "derivation_steps": [],
            "confidence_indicators": {},
        },
        "audit_metadata": {
            "request_id": "test-uuid",
            "generated_at_utc": "2026-02-22T00:00:00+00:00",
            "engine_version": "0.1.0",
        },
    }


# ── CompanyProfiler ───────────────────────────────────────────────────


class TestCompanyProfilerStageClassification(unittest.TestCase):
    """Verify deterministic stage classification rules."""

    def test_pre_seed_by_age(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "BabyCo",
            "as_of_date": "2026-02-22",
            "inputs": {},
        }
        metadata: dict[str, Any] = {"founded_date": "2025-06-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "pre_seed")
        self.assertIn("age < 18 months", profile.profile_summary)

    def test_pre_seed_no_revenue_no_round(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "NothingCo",
            "as_of_date": "2026-02-22",
            "inputs": {},
        }
        profile = CompanyProfiler.build_from_dict(assembled)
        self.assertEqual(profile.stage, "pre_seed")
        self.assertIn("no revenue and no institutional round", profile.profile_summary)

    def test_seed_stage(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "SeedCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 500000,
                "last_post_money_valuation": 3000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2024-06-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "seed")

    def test_early_stage_by_age_and_revenue(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "EarlyCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 5000000,
                "last_post_money_valuation": 30000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2023-06-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "early")

    def test_early_stage_by_round_size(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "EarlyCo2",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 2000000,
                "last_post_money_valuation": 20000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2018-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "early")

    def test_growth_stage_by_revenue(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "GrowthCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 50000000,
                "last_post_money_valuation": 200000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2020-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "growth")

    def test_growth_stage_by_post_money(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "GrowthCo2",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 8000000,
                "last_post_money_valuation": 150000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2015-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "growth")

    def test_late_stage_by_revenue(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "LateCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 200000000,
                "last_post_money_valuation": 2000000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2012-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "late")

    def test_late_stage_by_post_money(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "LateCo2",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 50000000,
                "last_post_money_valuation": 600000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2018-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(profile.stage, "late")

    def test_default_growth_when_ambiguous(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "AmbiguousCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "revenue_ltm": 500000,
                "last_post_money_valuation": 60000000,
            },
        }
        metadata: dict[str, Any] = {"founded_date": "2014-01-01"}
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        # Age >6yr, has revenue <$1M, post-money $60M — growth by post-money rule
        self.assertEqual(profile.stage, "growth")


class TestCompanyProfilerFields(unittest.TestCase):
    """Verify profile fields are populated correctly."""

    def test_round_age_calculation(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "RoundCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_round_date": "2025-08-22",
                "last_post_money_valuation": 50000000,
            },
        }
        profile = CompanyProfiler.build_from_dict(assembled)
        self.assertIsNotNone(profile.last_round_age_months)
        # ~6 months
        self.assertAlmostEqual(profile.last_round_age_months, 6.0, delta=0.5)  # type: ignore[arg-type]

    def test_sources_used(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "SrcCo",
            "inputs": {},
        }
        metadata: dict[str, Any] = {
            "sources_consulted": ["pitchbook", "sec_filings", "crunchbase"],
        }
        profile = CompanyProfiler.build_from_dict(assembled, metadata)
        self.assertEqual(
            profile.sources_used,
            ("pitchbook", "sec_filings", "crunchbase"),
        )

    def test_government_contracts(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "GovCo",
            "inputs": {
                "government_contracts_usd": 5000000,
                "revenue_ltm": 10000000,
                "last_post_money_valuation": 50000000,
            },
        }
        profile = CompanyProfiler.build_from_dict(assembled)
        self.assertEqual(
            profile.government_contracts_usd,
            Decimal("5000000"),
        )


# ── DataPackage ────────────────────────────────────────────────────────


class TestDataPackageFromAssembled(unittest.TestCase):
    def test_basic_assembly(self) -> None:
        assembled: dict[str, Any] = {
            "company_name": "TestCo",
            "as_of_date": "2026-02-22",
            "inputs": {
                "last_post_money_valuation": 100000000,
                "last_round_date": "2025-06-01",
                "revenue_ltm": 15000000,
                "sector": "enterprise_software",
                "peer_set_quality": "HIGH",
                "private_company_discount_pct": 20,
                "public_index": "RUSSELL_2000",
                "target_description": "API security platform for fintech teams",
            },
        }
        dp = DataPackage.from_assembled_request(assembled)
        self.assertEqual(dp.last_post_money, Decimal("100000000"))
        self.assertEqual(dp.last_round_date, date(2025, 6, 1))
        self.assertEqual(dp.revenue_ltm, Decimal("15000000"))
        self.assertEqual(dp.sector, "enterprise_software")
        self.assertEqual(dp.peer_set_quality, "HIGH")
        self.assertEqual(dp.private_company_discount_pct, Decimal("20"))
        self.assertEqual(dp.public_index, "RUSSELL_2000")
        self.assertEqual(dp.target_description, "API security platform for fintech teams")

    def test_defaults(self) -> None:
        assembled: dict[str, Any] = {"inputs": {}}
        dp = DataPackage.from_assembled_request(assembled)
        self.assertIsNone(dp.last_post_money)
        self.assertEqual(dp.private_company_discount_pct, Decimal("25"))
        self.assertEqual(dp.public_index, "NASDAQ_COMPOSITE")
        self.assertEqual(dp.sector, "enterprise_software")

    def test_override_as_of_date(self) -> None:
        assembled: dict[str, Any] = {
            "as_of_date": "2025-01-01",
            "inputs": {},
        }
        dp = DataPackage.from_assembled_request(assembled, as_of_date=date(2026, 6, 15))
        self.assertEqual(dp.as_of_date, date(2026, 6, 15))


# ── MethodologySelector ───────────────────────────────────────────────


class TestMethodologySelectorStages(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = MethodologySelector()

    def test_pre_seed_only_scorecard_and_berkus(self) -> None:
        profile = _make_profile(
            stage="pre_seed",
            has_revenue=False,
            last_post_money=None,
        )
        dp = _make_data_package(
            revenue_ltm=None,
            last_post_money=None,
            last_round_date=None,
            regional_median=Decimal("2000000"),
            scorecard_factors={
                "team": 1.0,
                "opportunity": 1.0,
                "product": 1.0,
                "competitive": 1.0,
                "marketing": 1.0,
                "investment_need": 1.0,
                "other": 1.0,
            },
            berkus_factors={
                "sound_idea": True,
                "prototype": True,
                "quality_management": True,
                "strategic_relationships": True,
                "product_rollout": False,
            },
            max_pre_money=Decimal("2000000"),
        )
        plan = self.selector.select(profile, dp)
        method_names = {w.methodology for w in plan.weights if w.weight > 0}
        self.assertEqual(method_names, {"scorecard", "berkus"})
        total = sum(w.weight for w in plan.weights if w.weight > 0)
        self.assertAlmostEqual(float(total), 1.0, places=3)

    def test_seed_excludes_ratchet(self) -> None:
        profile = _make_profile(
            stage="seed",
            has_revenue=True,
            estimated_arr=Decimal("500000"),
            last_post_money=Decimal("3000000"),
        )
        dp = _make_data_package(
            revenue_ltm=Decimal("500000"),
            last_post_money=Decimal("3000000"),
            last_round_date=date(2025, 8, 1),
            regional_median=Decimal("2000000"),
            scorecard_factors={
                "team": 1.0,
                "opportunity": 1.0,
                "product": 1.0,
                "competitive": 1.0,
                "marketing": 1.0,
                "investment_need": 1.0,
                "other": 1.0,
            },
            berkus_factors={
                "sound_idea": True,
                "prototype": True,
                "quality_management": True,
                "strategic_relationships": True,
                "product_rollout": True,
            },
            max_pre_money=Decimal("2000000"),
        )
        plan = self.selector.select(profile, dp)
        method_names = {w.methodology for w in plan.weights if w.weight > 0}
        self.assertNotIn("last_round_multiple_ratchet", method_names)

    def test_growth_stage_default_weights(self) -> None:
        profile = _make_profile(stage="growth")
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        method_names = {w.methodology for w in plan.weights if w.weight > 0}
        # Growth: comps + last_round — both should have data requirements met
        self.assertIn("comparable_companies", method_names)
        self.assertIn("last_round_market_adjusted", method_names)

    def test_late_stage_weights_sum_to_one(self) -> None:
        profile = _make_profile(stage="late")
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        total = sum(w.weight for w in plan.weights if w.weight > 0)
        self.assertAlmostEqual(float(total), 1.0, places=3)


class TestMethodologySelectorDataRules(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = MethodologySelector()

    def test_no_revenue_excludes_comps(self) -> None:
        profile = _make_profile(
            stage="early",
            has_revenue=False,
            estimated_arr=None,
        )
        dp = _make_data_package(
            revenue_ltm=None,
            last_post_money=Decimal("30000000"),
            last_round_date=date(2025, 6, 1),
        )
        plan = self.selector.select(profile, dp)
        method_names = {w.methodology for w in plan.weights if w.weight > 0}
        self.assertNotIn("comparable_companies", method_names)

    def test_stale_round_over_3_years_excluded(self) -> None:
        profile = _make_profile(
            stage="growth",
            last_round_age_months=40.0,
        )
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        method_names = {w.methodology for w in plan.weights if w.weight > 0}
        self.assertNotIn("last_round_market_adjusted", method_names)

    def test_fresh_round_high_modifier(self) -> None:
        profile = _make_profile(
            stage="growth",
            last_round_age_months=3.0,
        )
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        # last_round should have increased weight relative to base
        lr_weight = None
        for w in plan.weights:
            if w.methodology == "last_round_market_adjusted" and w.weight > 0:
                lr_weight = w.weight
        self.assertIsNotNone(lr_weight)
        # After HIGH modifier (1.25x) and normalisation the last_round weight
        # should be > 0.40 (its base weight is 0.40 but boosted)
        self.assertGreater(float(lr_weight), 0.40)  # type: ignore[arg-type]

    def test_no_applicable_methods_raises(self) -> None:
        profile = _make_profile(
            stage="growth",
            has_revenue=False,
            last_round_age_months=40.0,  # stale → exclude last_round
        )
        dp = _make_data_package(
            revenue_ltm=None,  # no comps data
            last_post_money=None,  # no last_round data
            last_round_date=None,
        )
        with self.assertRaises(DataSourceError):
            self.selector.select(profile, dp)


class TestMethodologySelectorPlanProperties(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = MethodologySelector()

    def test_selector_version(self) -> None:
        profile = _make_profile()
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        self.assertEqual(plan.selector_version, "v1.0")

    def test_applicable_count(self) -> None:
        profile = _make_profile()
        dp = _make_data_package()
        plan = self.selector.select(profile, dp)
        self.assertEqual(
            plan.applicable_count,
            sum(1 for w in plan.weights if w.data_requirements_met),
        )


# ── Reconciler ─────────────────────────────────────────────────────────


class TestReconcilerWeightedAverage(unittest.TestCase):
    def test_single_methodology(self) -> None:
        results = {"scorecard": _fake_result(5_000_000)}
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="scorecard",
                    weight=Decimal("1.0"),
                    rationale="only method",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=1,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        self.assertEqual(
            summary.concluded_value.point_estimate,
            Decimal("5000000.00"),
        )

    def test_two_methodologies_weighted(self) -> None:
        results = {
            "comparable_companies": _fake_result(10_000_000),
            "last_round_market_adjusted": _fake_result(8_000_000),
        }
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="comparable_companies",
                    weight=Decimal("0.60"),
                    rationale="comps",
                    data_requirements_met=True,
                ),
                MethodologyWeight(
                    methodology="last_round_market_adjusted",
                    weight=Decimal("0.40"),
                    rationale="last round",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=2,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        # 0.60 * 10M + 0.40 * 8M = 6M + 3.2M = 9.2M
        self.assertEqual(
            summary.concluded_value.point_estimate,
            Decimal("9200000.00"),
        )

    def test_range_derivation(self) -> None:
        results = {"comparable_companies": _fake_result(10_000_000)}
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="comparable_companies",
                    weight=Decimal("1.0"),
                    rationale="only",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=1,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        cv = summary.concluded_value
        # comps spread = ±25%  → low=7.5M, high=12.5M
        self.assertEqual(cv.range_low, Decimal("7500000.00"))
        self.assertEqual(cv.range_high, Decimal("12500000.00"))


class TestReconcilerDivergence(unittest.TestCase):
    def test_no_divergence_similar_values(self) -> None:
        results = {
            "comparable_companies": _fake_result(10_000_000),
            "last_round_market_adjusted": _fake_result(9_500_000),
        }
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="comparable_companies",
                    weight=Decimal("0.50"),
                    rationale="comps",
                    data_requirements_met=True,
                ),
                MethodologyWeight(
                    methodology="last_round_market_adjusted",
                    weight=Decimal("0.50"),
                    rationale="last round",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=2,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        self.assertFalse(summary.divergence_flag)
        self.assertIsNone(summary.divergence_note)

    def test_divergence_detected(self) -> None:
        # 20M vs 5M → (20-5)/12.5 = 1.2 > 0.40
        results = {
            "comparable_companies": _fake_result(20_000_000),
            "last_round_market_adjusted": _fake_result(5_000_000),
        }
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="comparable_companies",
                    weight=Decimal("0.60"),
                    rationale="comps",
                    data_requirements_met=True,
                ),
                MethodologyWeight(
                    methodology="last_round_market_adjusted",
                    weight=Decimal("0.40"),
                    rationale="last round",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=2,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        self.assertTrue(summary.divergence_flag)
        self.assertIsNotNone(summary.divergence_note)
        self.assertIn("comparable_companies", summary.divergence_note or "")
        self.assertIn("Manual review recommended", summary.divergence_note or "")


class TestReconcilerRationale(unittest.TestCase):
    def test_rationale_includes_method_names(self) -> None:
        results = {
            "scorecard": _fake_result(3_000_000),
            "berkus": _fake_result(2_500_000),
        }
        plan = MethodologyPlan(
            weights=(
                MethodologyWeight(
                    methodology="scorecard",
                    weight=Decimal("0.50"),
                    rationale="scorecard base",
                    data_requirements_met=True,
                ),
                MethodologyWeight(
                    methodology="berkus",
                    weight=Decimal("0.50"),
                    rationale="berkus base",
                    data_requirements_met=True,
                ),
            ),
            selector_version="v1.0",
            applicable_count=2,
        )
        summary = Reconciler.reconcile(results, plan, date(2026, 2, 22))
        self.assertIn("scorecard", summary.reconciliation_rationale)
        self.assertIn("berkus", summary.reconciliation_rationale)
        self.assertIn("Concluded value", summary.reconciliation_rationale)


# ── ReconciliationEngine (integration) ─────────────────────────────────


class TestReconciliationEngineIntegration(unittest.TestCase):
    """Integration tests using real methodologies and mock data sources."""

    def test_growth_stage_two_methods(self) -> None:
        from vc_audit_tool.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine.mock()
        profile = _make_profile(stage="growth")
        dp = _make_data_package(
            revenue_ltm=Decimal("15000000"),
            last_post_money=Decimal("100000000"),
            last_round_date=date(2025, 6, 1),
        )
        rv = engine.value(
            profile=profile,
            data_package=dp,
            as_of_date=date(2026, 2, 22),
            company_name="TestCo",
        )
        self.assertIsInstance(rv, ReconciledValuation)
        cv = rv.reconciliation.concluded_value
        self.assertGreater(cv.point_estimate, 0)
        self.assertLessEqual(cv.range_low, cv.point_estimate)
        self.assertGreaterEqual(cv.range_high, cv.point_estimate)
        self.assertEqual(cv.currency, "USD")

    def test_target_description_is_forwarded_to_comps_method(self) -> None:
        from vc_audit_tool.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine.mock()
        profile = _make_profile(stage="growth")
        dp = _make_data_package(
            revenue_ltm=Decimal("15000000"),
            last_post_money=Decimal("100000000"),
            last_round_date=date(2025, 6, 1),
            target_description="workflow automation software for healthcare ops teams",
        )
        rv = engine.value(
            profile=profile,
            data_package=dp,
            as_of_date=date(2026, 2, 22),
            company_name="TestCo",
        )
        comps_result = rv.methodology_results["comparable_companies"]["valuation_result"]
        self.assertEqual(
            comps_result["inputs_used"]["target_description"],
            "workflow automation software for healthcare ops teams",
        )

    def test_pre_seed_scorecard_berkus(self) -> None:
        from vc_audit_tool.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine.mock()
        profile = _make_profile(
            stage="pre_seed",
            has_revenue=False,
            last_post_money=None,
        )
        dp = _make_data_package(
            revenue_ltm=None,
            last_post_money=None,
            last_round_date=None,
            regional_median=Decimal("2000000"),
            scorecard_factors={
                "team": 1.2,
                "opportunity": 1.0,
                "product": 0.8,
                "competitive": 1.0,
                "marketing": 0.9,
                "investment_need": 1.1,
                "other": 1.0,
            },
            berkus_factors={
                "sound_idea": True,
                "prototype": 0.5,
                "quality_management": True,
                "strategic_relationships": False,
                "product_rollout": False,
            },
            max_pre_money=Decimal("2500000"),
        )
        rv = engine.value(
            profile=profile,
            data_package=dp,
            as_of_date=date(2026, 2, 22),
            company_name="PreSeedCo",
        )
        self.assertIsInstance(rv, ReconciledValuation)
        method_names = set(rv.methodology_results.keys())
        # Should only have scorecard and berkus
        for m in method_names:
            self.assertIn(m, {"scorecard", "berkus"})

    def test_to_dict_structure(self) -> None:
        from vc_audit_tool.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine.mock()
        profile = _make_profile(stage="growth")
        dp = _make_data_package()
        rv = engine.value(
            profile=profile,
            data_package=dp,
            as_of_date=date(2026, 2, 22),
            company_name="TestCo",
        )
        d = rv.to_dict()
        self.assertIn("concluded_value", d)
        self.assertIn("reconciliation", d)
        self.assertIn("methodology_results", d)
        self.assertIn("company_profile", d)
        self.assertIn("audit_metadata", d)
        # concluded_value fields
        cv = d["concluded_value"]
        self.assertIn("point_estimate", cv)
        self.assertIn("range_low", cv)
        self.assertIn("range_high", cv)
        self.assertIn("currency", cv)
        self.assertIn("as_of_date", cv)
        # reconciliation fields
        recon = d["reconciliation"]
        self.assertIn("methodology_weights", recon)
        self.assertIn("divergence_flag", recon)
        self.assertIn("selector_version", recon)

    def test_research_metadata_forwarded(self) -> None:
        from vc_audit_tool.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine.mock()
        profile = _make_profile(stage="growth")
        dp = _make_data_package()
        meta = {"sources_consulted": ["pitchbook"], "query_count": 5}
        rv = engine.value(
            profile=profile,
            data_package=dp,
            as_of_date=date(2026, 2, 22),
            company_name="TestCo",
            research_metadata=meta,
        )
        self.assertEqual(rv.research_metadata, meta)


# ── ReconciledValuation model ──────────────────────────────────────────


class TestReconciledValuationModel(unittest.TestCase):
    def test_to_dict_round_trip_fields(self) -> None:
        cv = ConcludedValue(
            point_estimate=Decimal("10000000"),
            range_low=Decimal("7500000"),
            range_high=Decimal("12500000"),
            currency="USD",
            as_of_date=date(2026, 2, 22),
        )
        summary = ReconciliationSummary(
            concluded_value=cv,
            methodology_weights=(
                MethodologyWeight(
                    methodology="comps",
                    weight=Decimal("1.0"),
                    rationale="only method",
                    data_requirements_met=True,
                ),
            ),
            divergence_flag=False,
            divergence_note=None,
            reconciliation_rationale="test",
            selector_version="v1.0",
        )
        profile = _make_profile()
        rv = ReconciledValuation(
            reconciliation=summary,
            methodology_results={"comps": _fake_result(10_000_000)},
            company_profile=profile,
        )
        d = rv.to_dict()
        self.assertEqual(d["concluded_value"]["point_estimate"], 10_000_000.0)
        self.assertEqual(d["concluded_value"]["as_of_date"], "2026-02-22")
        self.assertEqual(d["company_profile"]["name"], "TestCo")
        self.assertEqual(d["company_profile"]["stage"], "growth")


if __name__ == "__main__":
    unittest.main()
