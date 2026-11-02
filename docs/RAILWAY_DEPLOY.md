# 🚀 Deploy to Railway

## Quick Setup (5 minutes)

### 1. Push your changes to GitHub
```bash
cd f:\code\projects\live-feed-quantizer
git add .
git commit -m "Add Railway deployment config and dynamic WebSocket URL"
git push origin main
```

### 2. Go to Railway
- Visit: https://railway.app
- Click **"Start a New Project"**
- Select **"Deploy from GitHub repo"**
- Authenticate with GitHub
- Select your **live-feed-quantizer** repository

### 3. Configure on Railway
- Railway will auto-detect the Dockerfile
- It will automatically set up:
  - Build: Uses Dockerfile
  - Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
  - Port: Auto-assigned (from $PORT environment variable)

### 4. Deploy
- Click **"Deploy"**
- Wait 3-5 minutes for build to complete
- You'll get a URL like: `https://live-feed-quantizer-production.up.railway.app`

### 5. Test Your App
- Open the URL in your browser
- Click "Start" and allow camera/microphone access
- You should see the live feed with audio-driven colors!

---

## What Changed

✅ **WebSocket URL** now auto-detects the deployed domain
✅ **Dockerfile** included for Railway build
✅ **railway.json** config file added
✅ **Requirements.txt** verified

---

## Troubleshooting

### Build fails?
- Check Railway logs in dashboard
- Make sure all files are pushed to GitHub

### WebSocket connection error?
- Make sure you're using `https://` (not `http://`)
- The WebSocket will upgrade to `wss://` automatically
- Check browser console for exact error

### Camera access denied?
- Browser may block camera on non-localhost
- Allow permissions when prompted
- Some browsers require HTTPS (Railway provides this)

---

## Cost
- Free tier: **$5 USD credit/month**
- Your app will use approximately **$2-3/month** if always running
- You get **$5 free credit** so it's covered!

---

## Need Help?
- Check Railway logs: Dashboard → Your Project → Deployments
- Join Railway Discord for support

Good luck! 🎉
