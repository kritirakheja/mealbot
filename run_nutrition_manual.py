from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from nutrition import analyze_meal_image

result = analyze_meal_image(
    Path("uploads/b22e0a93f6781dda50c35207454e282c6287704d8b8bea19dbe8a977611f89ce.jpg"),
    "image/jpeg",
)
print(result.model_dump_json(indent=2))
