# Idan+ for Stremio

זה תוסף Stremio שמתרגם את ההרחבה `plugin.video.idanplus` למבנה של Stremio עם עדכון אוטומטי של התוכן מהמקורות החיים של ההרחבה.

מה בפנים עכשיו:
- `עידן פלוס - טלוויזיה`
- `עידן פלוס - רדיו`
- `עידן פלוס - VOD` עם תפריטי משנה של כאן / קשת-מאקו / רשת 13 / עכשיו 14 / כאן חינוכית / כאן ארכיון / ערוץ 24 / i24NEWS / ערוץ 9 / ספורט 5 / ספורט 1
- `עידן פלוס - תכניות רדיו`
- `עידן פלוס - פודקאסטים`
- `עידן פלוס - מוזיקה`
- `עידן פלוס - מועדפים`
- `עידן פלוס - חיפוש`
- טעינה חמה של `channels.json`
- שימוש במודולים המקוריים של ההרחבה דרך שכבת תאימות ל־Stremio

מה לא זהה ל־Kodi:
- אין UI היררכי זהה של Kodi
- אין context menus
- אין מועדפים פר־משתמש כמו ב־Kodi
- אין יצוא IPTV/EPG

## הרצה מקומית

```bash
cd idanplus_stremio_live
python3 -m pip install -r requirements.txt
python3 app.py
```

ברירת המחדל היא פורט `8090`.

## התקנה בסטרימיו

הדבק בשדה **Add-on Repository URL** את:

```text
http://127.0.0.1:8090/manifest.json
```

אם אתה מריץ ב־Render או שרת אחר, תשתמש בכתובת הציבורית.

## Render / GitHub

- מעלים את כל התיקייה לריפו
- Render עושה deploy אוטומטי על כל push
- חשוב להגדיר `PUBLIC_BASE_URL` לכתובת ה־`onrender.com` שלך

## משתני סביבה חשובים

```bash
PORT=8090
PUBLIC_BASE_URL=https://your-service.onrender.com
IDANPLUS_REFRESH_SECONDS=60
IDANPLUS_CHANNELS_URL=https://raw.githubusercontent.com/Fishenzon/repo/master/zips/plugin.video.idanplus/channels.json
ALLOW_REMOTE_REFRESH=true
MAX_META_VIDEOS=240
MAX_RECURSION_DEPTH=6
```

## עדכון אוטומטי

כל מה שיושב מאחורי אותם endpoints מתעדכן אוטומטית דרך השרת:
- ערוצים
- לינקים
- רשימות VOD
- פודקאסטים
- תכניות רדיו
- מוזיקה

אם משנים את `manifest.json` עצמו, צריך להדביק שוב את אותה כתובת manifest בתוך Stremio כדי לרענן את המבנה החדש.

## מבנה התיקייה

```text
app.py
requirements.txt
render.yaml
assets/
data/
resources/
xbmc.py
xbmcplugin.py
xbmcgui.py
xbmcaddon.py
```
