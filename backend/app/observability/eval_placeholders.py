"""
Evaluation Placeholders
========================

Stubs for DeepEval and Ragas integration.
These functions are wired into the chat pipeline but return dummy metrics
until the actual eval frameworks are installed and configured.

To activate:
  1. `pip install deepeval ragas`
  2. Replace the stub implementations below with real logic.
  3. Set EVAL_ENABLED=true in .env

Tracked metrics:
  - symptom_extraction_accuracy  : did the specialist correctly capture reported symptoms?
  - diagnosis_relevance          : is the AI diagnosis relevant to the symptoms?
  - booking_success_rate         : % of consultations that end with a confirmed booking
  - follow_up_efficiency         : avg number of follow-up Q's before a diagnosis
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    metric: str
    score: float           # 0.0 – 1.0
    passed: bool
    reason: Optional[str] = None
    raw: dict = field(default_factory=dict)


# ── DeepEval stubs ────────────────────────────────────────────────────────────

async def eval_symptom_extraction(
    patient_message: str,
    extracted_symptoms: str,
) -> EvalResult:
    """
    Placeholder: measure whether extracted_symptoms correctly reflects
    what the patient described.

    TODO: Implement with deepeval.metrics.HallucinationMetric or
          a custom GEval metric that scores symptom coverage.

    Example (when deepeval is installed):
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
        metric = GEval(
            name="SymptomExtraction",
            criteria="Does the extracted symptoms text accurately cover all symptoms the patient mentioned?",
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        )
        test_case = LLMTestCase(input=patient_message, actual_output=extracted_symptoms)
        metric.measure(test_case)
        return EvalResult(metric="symptom_extraction", score=metric.score, passed=metric.is_successful())
    """
    logger.debug("[eval_stub] symptom_extraction – returning dummy score")
    return EvalResult(metric="symptom_extraction_accuracy", score=0.0, passed=False, reason="stub")


async def eval_diagnosis_relevance(
    symptoms: str,
    ai_diagnosis: str,
) -> EvalResult:
    """
    Placeholder: measure whether ai_diagnosis is clinically plausible for symptoms.

    TODO: Implement with Ragas AnswerRelevancy or DeepEval FaithfulnessMetric.

    Example (Ragas):
        from ragas.metrics import answer_relevancy
        from ragas import evaluate
        from datasets import Dataset
        data = {"question": [symptoms], "answer": [ai_diagnosis], "contexts": [[]]}
        result = evaluate(Dataset.from_dict(data), metrics=[answer_relevancy])
        score = result["answer_relevancy"]
        return EvalResult(metric="diagnosis_relevance", score=score, passed=score > 0.7)
    """
    logger.debug("[eval_stub] diagnosis_relevance – returning dummy score")
    return EvalResult(metric="diagnosis_relevance", score=0.0, passed=False, reason="stub")


# ── Aggregated pipeline evaluation ────────────────────────────────────────────

async def run_pipeline_eval(
    patient_message: str,
    extracted_symptoms: str,
    ai_diagnosis: str,
) -> list[EvalResult]:
    """Run all eval metrics for a completed consultation turn."""
    results = []
    results.append(await eval_symptom_extraction(patient_message, extracted_symptoms))
    results.append(await eval_diagnosis_relevance(extracted_symptoms, ai_diagnosis))
    return results


def log_eval_results(results: list[EvalResult]) -> None:
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        logger.info(f"[eval] {r.metric}: {r.score:.2f} [{status}] {r.reason or ''}")
