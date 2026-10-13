# CommunityOS

## Why create CommunityOS

I'm an international student living in Kitchener-Waterloo region, and I can totally understand some new international students or new immigrates feel confused about a new country and where they should turn to when they have different problems.



## Use LLM to fetch data

Before using LLM, make sure you've already downloaded Gemini SDK and got your own key. For downloading: 

```sh
python -m pip install -r requirements.txt
```

or just downloading SDK: 

```sh
python -m pip install google-genai
```

Then set up environment variables

```sh
$env:GEMINI_API_KEY = "Your Gemini API key"
$env:GEMINI_MODEL = "gemini-3.8-flash"
# Search uses local rules; Gemini is reserved for webpage ingestion.
$env:USE_LLM_INTENT = "0"
$env:ADMIN_TOKEN = "Set a admin token for yourself so you can enter admin version"
```

With all those set up, you can fetch data through websites in sources.json (or you can add other websites in it)

```sh
python -m backend.ingest --sources-file data/sources.json
```



## Run CommunityOS

```sh
python -m backend.simple_server
```

open http://localhost:8000 to see

have a local admin version http://localhost:8000/admin maybe later will make it to a website
