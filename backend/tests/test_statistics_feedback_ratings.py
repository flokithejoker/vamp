from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.providers.elevenlabs import ElevenLabsConversationListPayload
from app.storage.feedback_store import submit_call_rating


class StatisticsFeedbackRatingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "feedback.sqlite3"
        os.environ["FEEDBACK_DB_PATH"] = str(self.db_path)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()
        os.environ.pop("FEEDBACK_DB_PATH", None)

    def test_statistics_uses_feedback_db_ratings(self) -> None:
        now_unix = int(datetime.now(tz=timezone.utc).timestamp())
        payload = ElevenLabsConversationListPayload(
            conversations=[
                {
                    "conversation_id": "conv_a",
                    "metadata": {
                        "start_time_unix_secs": now_unix - 120,
                        "feedback": {"rating": 1},
                        "charging": {"total_cost": {"amount": 1.2, "currency": "USD"}},
                    },
                    "status": "done",
                },
                {
                    "conversation_id": "conv_b",
                    "metadata": {
                        "start_time_unix_secs": now_unix - 240,
                        "feedback": {"rating": 5},
                        "charging": {"total_cost": {"amount": 2.4, "currency": "USD"}},
                    },
                    "status": "done",
                },
            ],
            has_more=False,
            next_cursor=None,
        )
        submit_call_rating(call_id="conv_a", rating=4)

        with patch(
            "app.modules.statistics.list_agent_conversations",
            new=AsyncMock(return_value=payload),
        ):
            response = self.client.get("/api/statistics/overview?timeline=1d&currency=USD")

        self.assertEqual(response.status_code, 200)
        metrics = response.json()["metrics"]
        self.assertEqual(metrics["totalCalls"], 2)
        self.assertEqual(metrics["ratedCalls"], 1)
        self.assertEqual(metrics["averageRating"], 4.0)


if __name__ == "__main__":
    unittest.main()

