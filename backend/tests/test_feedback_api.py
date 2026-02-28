from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


class FeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "feedback.sqlite3"
        os.environ["FEEDBACK_DB_PATH"] = str(self.db_path)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()
        os.environ.pop("FEEDBACK_DB_PATH", None)

    def test_submit_call_rating_persists(self) -> None:
        response = self.client.post(
            "/api/feedback/submit_call_rating",
            json={"call_id": "conv_1", "rating": 4},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rating"], 4)

        get_response = self.client.get("/api/feedback/calls/conv_1")
        self.assertEqual(get_response.status_code, 200)
        item = get_response.json()["item"]
        self.assertEqual(item["callId"], "conv_1")
        self.assertEqual(item["rating"], 4)
        self.assertIsNone(item["comment"])

    def test_submit_call_feedback_persists(self) -> None:
        response = self.client.post(
            "/api/feedback/submit_call_feedback",
            json={"call_id": "conv_2", "comment": "Could be a bit faster."},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["comment"], "Could be a bit faster.")

        get_response = self.client.get("/api/feedback/calls/conv_2")
        self.assertEqual(get_response.status_code, 200)
        item = get_response.json()["item"]
        self.assertEqual(item["callId"], "conv_2")
        self.assertEqual(item["comment"], "Could be a bit faster.")
        self.assertIsNone(item["rating"])

    def test_submit_upsert_overwrites_previous_values(self) -> None:
        first_rating = self.client.post(
            "/api/feedback/submit_call_rating",
            json={"call_id": "conv_3", "rating": 2},
        )
        self.assertEqual(first_rating.status_code, 200)

        updated_rating = self.client.post(
            "/api/feedback/submit_call_rating",
            json={"call_id": "conv_3", "rating": 5},
        )
        self.assertEqual(updated_rating.status_code, 200)

        first_comment = self.client.post(
            "/api/feedback/submit_call_feedback",
            json={"call_id": "conv_3", "comment": "Old note"},
        )
        self.assertEqual(first_comment.status_code, 200)

        updated_comment = self.client.post(
            "/api/feedback/submit_call_feedback",
            json={"call_id": "conv_3", "comment": "Latest note"},
        )
        self.assertEqual(updated_comment.status_code, 200)

        get_response = self.client.get("/api/feedback/calls/conv_3")
        self.assertEqual(get_response.status_code, 200)
        item = get_response.json()["item"]
        self.assertEqual(item["rating"], 5)
        self.assertEqual(item["comment"], "Latest note")

    def test_invalid_rating_is_rejected(self) -> None:
        response = self.client.post(
            "/api/feedback/submit_call_rating",
            json={"call_id": "conv_invalid", "rating": 9},
        )
        self.assertEqual(response.status_code, 422)

    def test_blank_comment_is_rejected(self) -> None:
        response = self.client.post(
            "/api/feedback/submit_call_feedback",
            json={"call_id": "conv_blank", "comment": "    "},
        )
        self.assertEqual(response.status_code, 422)

    def test_monitoring_detail_includes_feedback(self) -> None:
        rating_response = self.client.post(
            "/api/feedback/submit_call_rating",
            json={"call_id": "conv_42", "rating": 4},
        )
        self.assertEqual(rating_response.status_code, 200)

        feedback_response = self.client.post(
            "/api/feedback/submit_call_feedback",
            json={"call_id": "conv_42", "comment": "Great support."},
        )
        self.assertEqual(feedback_response.status_code, 200)

        mock_conversation = {
            "conversation_id": "conv_42",
            "status": "done",
            "metadata": {
                "start_time_unix_secs": 1730000000,
                "call_duration_secs": 65,
            },
            "analysis": {
                "transcript_summary": "Resolved booking question.",
            },
            "transcript": [],
        }

        with patch(
            "app.modules.monitoring._fetch_conversation_for_agent",
            new=AsyncMock(return_value=mock_conversation),
        ):
            response = self.client.get("/api/monitoring/conversations/conv_42")

        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertIn("feedback", item)
        self.assertEqual(item["feedback"]["rating"], 4)
        self.assertEqual(item["feedback"]["comment"], "Great support.")


if __name__ == "__main__":
    unittest.main()

