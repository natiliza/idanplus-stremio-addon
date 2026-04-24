# Idan+ for Stremio — Live + Radio only

הגרסה הזאת כוללת רק:
- עידן פלוס - טלוויזיה
- עידן פלוס - רדיו

אין VOD, אין פודקאסטים, ואין תפריטים כבדים.

## Render

- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py`
- Environment variable חובה: `PUBLIC_BASE_URL=https://YOUR-SERVICE.onrender.com`

אחרי deploy:

`https://YOUR-SERVICE.onrender.com/manifest.json`

את הכתובת הזאת מדביקים ב-Stremio בשדה Add-on Repository URL.
