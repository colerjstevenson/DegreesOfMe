Actor → Movie Connection Finder — Webapp

This is a static single-page webapp port of your desktop PyQt app that searches for a path between an actor and one of several goal movies using TMDb's API and a meet-in-the-middle search.

Important: TMDb API Key
- The webapp requires a TMDb API key. For a quick test, add your API key to `app.js` in the `TMDB_API_KEY` constant.
- Hosting the static app on GitHub Pages will expose the API key publicly. Consider using a server-side proxy for production to keep the key secret.

How to run locally
1. In PowerShell run a simple static server. If you have Python installed:

```powershell
# from workspace root (c:\Users\coler\Documents\Coding\Degrees of me)
python -m http.server 8000 --directory .\webapp
# then open http://localhost:8000 in your browser
```

2. Edit the `app.js` file to insert your TMDb API key (see the top of the file).

Deploy to GitHub Pages (quick)
- Create a git repo, commit the `webapp/` contents, and push to GitHub.
- In repository settings -> Pages, set the source to the `main` branch and `/docs` or `gh-pages` branch depending on preference. Alternatively move files to `docs/` root.

Security and rate limits
- Because the key is public in a static site, other users can see and use it. Use low concurrency, caching, and small caps for MAX_* values.
- For production, implement a small server to proxy TMDb requests and keep the key on the server.

Files
- `index.html` — UI
- `styles.css` — styles
- `app.js` — search logic and TMDb client (insert your key here)

If you want, I can:
- Add a small server implementation (Node/Express) and deployment instructions to host the backend on Vercel/Render so the key is hidden.
- Wire up selection for multiple actor search results.
- Add caching to localStorage to persist excludes between runs.