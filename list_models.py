import sys

from google import genai
from google.genai import errors
from config import GEMINI_API_KEY


def main() -> int:
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is missing from .env", file=sys.stderr)
        return 1

    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        models = client.models.list()
        supported_models = [
            model.name
            for model in models
            if "generateContent" in (model.supported_actions or [])
        ]
    except errors.APIError as error:
        message = str(error).replace(GEMINI_API_KEY, "[REDACTED]")
        print(f"Could not list Gemini models: {message}", file=sys.stderr)
        return 1

    if not supported_models:
        print("No models supporting generateContent were returned.", file=sys.stderr)
        return 1

    print("\nAVAILABLE GEMINI MODELS\n")
    for model_name in supported_models:
        print(model_name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
