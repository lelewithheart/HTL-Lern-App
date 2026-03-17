"""AI service using a local Ollama instance for question generation and grading."""

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.3:8b")
# Ollama can take a long time to load a model the first time
REQUEST_TIMEOUT = 300  # seconds


class OllamaProvider:
    """Wraps the Ollama HTTP API for LLM-based operations."""

    def __init__(
        self,
        base_url: str = OLLAMA_URL,
        model: str = OLLAMA_MODEL,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post(self, prompt: str, system: str) -> str:
        """Send a prompt to Ollama and return the full response text."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.base_url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
        except httpx.TimeoutException as exc:
            logger.error("Ollama request timed out: %s", exc)
            raise RuntimeError(
                "The AI service did not respond in time. Please try again later."
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Ollama HTTP error %s: %s", exc.response.status_code, exc)
            raise RuntimeError(
                f"AI service returned an error: {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Ollama connection error: %s", exc)
            raise RuntimeError(
                "Could not connect to the AI service. Is Ollama running?"
            ) from exc

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Extract and parse the first JSON object or array found in *text*."""
        text = text.strip()
        # Try the whole text first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find first { or [
        start_brace = text.find("{")
        start_bracket = text.find("[")
        starts = [i for i in (start_brace, start_bracket) if i != -1]
        if not starts:
            raise ValueError(f"No JSON found in AI response: {text[:200]}")

        start = min(starts)
        # Try slices from the starting character until the end
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue

        raise ValueError(f"Could not extract valid JSON from AI response: {text[:200]}")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def generate_questions(
        self, stoff_text: str, class_level: str, subject: str
    ) -> list[dict]:
        """Generate questions for the given learning material.

        Returns a list of question dicts, each containing at minimum:
          - text (str)
          - type ('mc' | 'open')
          - options (list[str] | null)
          - correct_answer_index (int | null)
          - sample_solution (str | null)
        """
        system = (
            "Du bist ein HTL-Professor. Analysiere den Text und entscheide selbst "
            "über die Anzahl der Fragen (MC und Offen), um das Thema vollständig "
            "abzudecken. Antworte ausschließlich in validem JSON."
        )
        prompt = (
            f"Klasse: {class_level}\n"
            f"Fach: {subject}\n\n"
            f"Lernstoff:\n{stoff_text}\n\n"
            "Erstelle Fragen zum obigen Lernstoff. "
            "Gib ein JSON-Array zurück. Jedes Element hat folgende Felder:\n"
            '  "text": "Fragetext",\n'
            '  "type": "mc" oder "open",\n'
            '  "options": ["Option A", "Option B", ...] oder null (nur für mc),\n'
            '  "correct_answer_index": 0 (null-basierter Index, nur für mc),\n'
            '  "sample_solution": "Musterlösung" (nur für open).\n'
            "Antworte NUR mit dem JSON-Array."
        )

        raw = self._post(prompt, system)
        questions = self._extract_json(raw)

        if not isinstance(questions, list):
            raise ValueError("AI did not return a JSON array of questions.")

        return questions

    def grade_answer(
        self,
        question: str,
        sample_solution: str,
        student_answer: str,
    ) -> dict:
        """Grade an open-ended student answer against the sample solution.

        Returns a dict with:
          - score (float 0-100)
          - feedback (str)
        """
        system = (
            "Du bist ein strenger aber fairer HTL-Professor. "
            "Bewerte die Schülerantwort semantisch im Vergleich zur Musterlösung. "
            "Antworte ausschließlich in validem JSON."
        )
        prompt = (
            f"Frage: {question}\n\n"
            f"Musterlösung: {sample_solution}\n\n"
            f"Schülerantwort: {student_answer}\n\n"
            "Gib ein JSON-Objekt mit folgenden Feldern zurück:\n"
            '  "score": <Zahl 0-100>,\n'
            '  "feedback": "<konstruktives Feedback auf Deutsch>".\n'
            "Antworte NUR mit dem JSON-Objekt."
        )

        raw = self._post(prompt, system)
        result = self._extract_json(raw)

        if not isinstance(result, dict):
            raise ValueError("AI did not return a JSON object for grading.")

        score = float(result.get("score", 0))
        feedback = str(result.get("feedback", ""))
        return {"score": max(0.0, min(100.0, score)), "feedback": feedback}
