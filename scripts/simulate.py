import argparse
import os

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PseudoGram 500-event simulator.")
    parser.add_argument("--webhook-url", required=True)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--duration-seconds", type=int, default=10)
    args = parser.parse_args()

    api_key = os.environ["PSEUDOGRAM_API_KEY"]
    base_url = os.environ.get("PSEUDOGRAM_BASE_URL", "https://pseudogram-api.onrender.com")
    headers = {"X-API-Key": api_key}

    with httpx.Client(timeout=30) as client:
        start = client.post(
            f"{base_url}/v1/simulate/start",
            json={
                "webhook_url": args.webhook_url,
                "count": args.count,
                "duration_seconds": args.duration_seconds,
            },
            headers=headers,
        )
        start.raise_for_status()
        run_id = start.json()["run_id"]
        print({"run_id": run_id})

        truth = client.get(f"{base_url}/v1/simulate/{run_id}/truth", headers=headers)
        truth.raise_for_status()
        print(truth.text)


if __name__ == "__main__":
    main()
