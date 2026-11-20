
from core.gemini_client import GeminiRequestError, ask_gemini

try:
    print("Connecting to AEGIS X intelligence...")

    response = ask_gemini(
        "Introduce yourself as the AI intelligence engine of AEGIS X "
        "in two short sentences."
    )

    print("\nGEMINI RESPONSE:")
    print(response)

    print("\nAEGIS X Gemini connection successful!")

except (GeminiRequestError, ValueError) as error:
    print("\nConnection failed:")
    print(error)
