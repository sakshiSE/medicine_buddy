"""Offline backend checks; external services are mocked, never contacted."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("TAVILY_API_KEY", None)

from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest
from backend.app import main
from backend.app.main import (
    _normalise, _split_ingredients, _source_quality, _evidence_highlights, chat, ChatRequest,
    health, lookup, LookupRequest, app,
)


class BackendChecks(unittest.TestCase):
    def setUp(self):
        main._CACHE.clear()

    def test_normalise(self):
        self.assertEqual(_normalise("Dolo 650 mg Tablet"), "dolo 650")

    def test_manual_composition_split(self):
        parts = _split_ingredients("Paracetamol 500 mg + Caffeine 65 mg")
        self.assertEqual(len(parts), 2)
        self.assertIn("Paracetamol", parts[0])
        self.assertIn("Caffeine", parts[1])
        vitamins = _split_ingredients("Vitamin B12 1000 mcg + Vitamin D3 60,000 IU")
        self.assertIn("B12", vitamins[0])
        self.assertIn("D3", vitamins[1])

    def test_source_ranking(self):
        self.assertGreater(_source_quality("https://www.who.int/page", "WHO")[1], _source_quality("https://random.example/page", "Blog")[1])

    def test_highlights_are_extracted_from_source_text(self):
        source = {"title": "Reference", "url": "https://www.who.int/example", "source_rank": 4,
                  "content": "This ingredient is used to relieve pain. It works by reducing a chemical signal. Warnings include allergy reactions."}
        highlights = _evidence_highlights([source])
        self.assertIn("used to relieve pain", highlights["what_it_does"][0]["text"])
        self.assertIn("works by", highlights["how_it_works"][0]["text"])
        self.assertIn("Warnings", highlights["precautions"][0]["text"])

    def test_chat_does_not_invent_without_evidence(self):
        result = asyncio.run(chat(ChatRequest(question="What is the dose?", evidence=[])))
        self.assertFalse(result["grounded"])
        self.assertIn("couldn't find enough", result["answer"])

    def test_chat_accepts_source_metadata(self):
        evidence = [{"title": "Reference", "url": "https://www.who.int/example", "content": "This source discusses the medicine precautions.", "score": 0.8, "source_rank": 4, "published_date": None}]
        result = asyncio.run(chat(ChatRequest(question="What precautions are mentioned?", evidence=evidence)))
        self.assertTrue(result["grounded"])

    def test_health_endpoint(self):
        result = asyncio.run(health())
        self.assertTrue(result["ok"])

    def test_api_route_aliases(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/health").status_code, 200)

    def test_unknown_product_does_not_get_fake_composition(self):
        with patch("backend.app.main._tavily_search", new=AsyncMock(return_value=[])), \
             patch("backend.app.main._openfda_search", new=AsyncMock(return_value=[])), \
             patch("backend.app.main._dailymed_search", new=AsyncMock(return_value=[])):
            scope = {"type": "http", "method": "POST", "path": "/lookup", "headers": [], "client": ("testclient", 1234), "server": ("testserver", 80), "scheme": "http", "query_string": b""}
            result = asyncio.run(lookup(LookupRequest(medicine_name="Unknown Product QXZ"), StarletteRequest(scope)))
        self.assertEqual(result["identity"]["level"], "unverified")
        self.assertEqual(result["composition_candidates"], [])
        self.assertEqual(result["ingredients"], [])

    def test_confirmed_composition_triggers_individual_ingredient_search(self):
        medicine_source = [{
            "title": "Example label",
            "url": "https://www.who.int/example",
            "content": "Example medicine reference composition active ingredients: Paracetamol 500 mg + Caffeine 65 mg",
            "source_type": "Recognised health reference",
            "source_rank": 4,
            "score": 0.9,
        }]
        with patch("backend.app.main._tavily_search", new=AsyncMock(return_value=medicine_source)), \
             patch("backend.app.main._openfda_search", new=AsyncMock(return_value=[])), \
             patch("backend.app.main._dailymed_search", new=AsyncMock(return_value=[])):
            scope = {"type": "http", "method": "POST", "path": "/lookup", "headers": [], "client": ("testclient", 1235), "server": ("testserver", 80), "scheme": "http", "query_string": b""}
            result = asyncio.run(lookup(LookupRequest(
                medicine_name="Example medicine",
                confirmed_composition="Paracetamol 500 mg + Caffeine 65 mg",
            ), StarletteRequest(scope)))
        self.assertEqual([x["name"] for x in result["ingredients"]], ["Paracetamol", "Caffeine"])
        self.assertEqual(result["composition_status"], "user_confirmed")

    def test_confirmed_composition_reuses_product_search_cache(self):
        medicine_source = [{
            "title": "Example label",
            "url": "https://www.who.int/example",
            "content": "Example medicine reference composition active ingredients: Paracetamol 500 mg + Caffeine 65 mg",
            "source_type": "Recognised health reference",
            "source_rank": 4,
            "score": 0.9,
        }]
        with patch("backend.app.main._tavily_search", new=AsyncMock(return_value=medicine_source)) as search_mock, \
             patch("backend.app.main._openfda_search", new=AsyncMock(return_value=[])), \
             patch("backend.app.main._dailymed_search", new=AsyncMock(return_value=[])):
            scope_one = {"type": "http", "method": "POST", "path": "/lookup", "headers": [], "client": ("testclient", 1240), "server": ("testserver", 80), "scheme": "http", "query_string": b""}
            scope_two = {"type": "http", "method": "POST", "path": "/lookup", "headers": [], "client": ("testclient", 1241), "server": ("testserver", 80), "scheme": "http", "query_string": b""}
            asyncio.run(lookup(LookupRequest(medicine_name="Example medicine"), StarletteRequest(scope_one)))
            asyncio.run(lookup(LookupRequest(medicine_name="Example medicine", confirmed_composition="Paracetamol 500 mg + Caffeine 65 mg"), StarletteRequest(scope_two)))
        # 1 product search + 2 ingredient searches + 1 combination search; no repeated product search.
        self.assertEqual(search_mock.await_count, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
