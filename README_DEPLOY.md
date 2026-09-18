# ClaraGate Demo Backend — Railway Deployment

## What this is
A small FastAPI service that runs the actual ClaraGate r5 engine server-side.
When Miko's browser calls it, Clara makes the real adjudication decision.

## Deploy to Railway (5 minutes)

### Step 1 — Create a GitHub repo
1. Go to github.com → New repository
2. Name it `claragate-demo-backend` (private is fine)
3. Don't initialize with README

### Step 2 — Push this folder to GitHub
In this folder, run:
```bash
git init
git add .
git commit -m "ClaraGate demo backend 0.1.0rc2"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/claragate-demo-backend.git
git push -u origin main
```

### Step 3 — Deploy to Railway
1. Go to railway.app and sign in (free account works)
2. Click "New Project" → "Deploy from GitHub repo"
3. Select `claragate-demo-backend`
4. Railway auto-detects Python and deploys

### Step 4 — Get your URL
Railway gives you a URL like:
`https://claragate-demo-backend-production.up.railway.app`

Click "Settings" → "Domains" to find it, or it shows in the deploy log.

### Step 5 — Test it
```bash
curl https://YOUR_RAILWAY_URL/health
```
Should return: `{"status":"ok","version":"0.1.0rc2","engine":"sealed_vNext15"}`

### Step 6 — Give the URL to Claude
Paste your Railway URL back to Claude. Claude will put it into the
frontend HTML so the demo page calls your live backend.

## That's it.
One deploy. Never touch it again. Clara runs server-side for every
browser visit to democlaragate.com.
