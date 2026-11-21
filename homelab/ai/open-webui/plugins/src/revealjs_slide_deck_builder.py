"""
title: Reveal.js Presentation Slide Deck Builder (Rich UI)
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://buymeacoffee.com/iamg30
version: 1.0.0
license: MIT
required_open_webui_version: 0.9.0
"""

from typing import List
from fastapi.responses import HTMLResponse


class Tools:
    def __init__(self):
        pass

    def create_presentation(
        self, deck_title: str, slides_html: List[str], theme: str = "dracula"
    ) -> tuple[HTMLResponse, str]:
        """
        Generates a live, interactive presentation slideshow.
        Call this tool when a user asks you to create a presentation, a slide deck, or a visual summary of a topic.

        :param deck_title: The title of the overall presentation.
        :param slides_html: A list of strings where each string is the HTML content of a single slide. Use <h2> for slide titles, <ul>/<li> for bullet points, and <p> for paragraphs. Keep content concise!
        :param theme: The visual theme. Valid options: 'black', 'white', 'league', 'beige', 'night', 'blood', 'moon', 'dracula', 'solarized'. Defaults to 'dracula'.
        """
        # Ensure the AI picks a valid theme
        valid_themes = [
            "black",
            "white",
            "league",
            "beige",
            "night",
            "blood",
            "moon",
            "dracula",
            "solarized",
        ]
        if theme.lower() not in valid_themes:
            theme = "dracula"

        # Build the HTML markup for the slides
        slides_markup = ""
        for slide in slides_html:
            # Wrap each AI-generated HTML string in a Reveal.js <section> tag
            slides_markup += f"<section>{slide}</section>\n"

        # Construct the self-contained Reveal.js HTML document
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{deck_title}</title>
    <!-- Official Reveal.js CDNs -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.0.4/reset.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.0.4/reveal.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.0.4/theme/{theme}.min.css">
    <style>
        body {{ 
            margin: 0; padding: 0; background: transparent; display: flex; 
        }}
        .deck-wrapper {{
            width: 100%;
            max-width: 850px;
            height: 500px;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            position: relative;
        }}
        .reveal {{
            width: 100%;
            height: 100%;
        }}
    </style>
</head>
<body>
    <div class="deck-wrapper">
        <div class="reveal">
            <div class="slides">
                {slides_markup}
            </div>
        </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/reveal.js/5.0.4/reveal.min.js"></script>
    <script>
        // Initialize the presentation in 'embedded' mode to fit perfectly inside the chat wrapper
        Reveal.initialize({{
            embedded: true,
            controls: true,
            progress: true,
            center: true,
            hash: false,
            transition: 'slide' // Options: none/fade/slide/convex/concave/zoom
        }});
    </script>
</body>
</html>"""

        headers = {"Content-Disposition": "inline"}
        response = HTMLResponse(content=html_content, headers=headers)

        return (
            response,
            f"Successfully rendered the interactive presentation deck titled '{deck_title}'.",
        )
