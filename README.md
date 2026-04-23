# Idan+ Live for Stremio

זה שלד עובד לתוסף Stremio שמתרגם את מנגנון הערוצים החיים של ההרחבה `plugin.video.idanplus` למבנה של Stremio.

מה כבר בפנים:
- `manifest.json` דינמי דרך `/manifest.json`
- שני קטלוגים: `Idan+ Live TV` ו־`Idan+ Radio`
- טעינה חמה של `channels.json` מה־GitHub raw של ההרחבה
- נפילה אוטומטית ל־`data/channels.json` המקומי אם GitHub לא זמין
- פותרים (resolvers) ללייב של המודולים העיקריים: `tv`, `radio`, `kan`, `reshet`, `keshet`, `14tv`, `hidabroot`, `sport5`, `i24news`, `glz`, `99fm`, `100fm`, `1064fm`
- תמיכה ב־`Referer` / `User-Agent` דרך `behaviorHints.proxyHeaders`

מה עדיין לא הומר:
- כל תפריטי ה־VOD
- חיפוש `series.json`
- מועדפים אישיים
- סידור/הסתרה פר משתמש כמו ב־Kodi
- יצוא IPTV / EPG
- פודקאסטים / מוזיקה / תכניות רדיו כקטלוגי Stremio נפרדים

## הרצה

```bash
cd idanplus_stremio_live
python3 app.py
```

ברירת המחדל היא פורט `8090`.

## התקנה בסטרימיו

הדבק בשדה **Add-on Repository URL** את:

```text
http://127.0.0.1:8090/manifest.json
```

אם מריצים את זה על שרת אחר, יש להחליף לכתובת הציבורית של השרת.

## משתני סביבה

```bash
export PORT=8090
export PUBLIC_BASE_URL="https://your-domain.example"
export IDANPLUS_REFRESH_SECONDS=60
export IDANPLUS_CHANNELS_URL="https://raw.githubusercontent.com/Fishenzon/repo/master/zips/plugin.video.idanplus/channels.json"
export ALLOW_REMOTE_REFRESH=true
```

## איך עובד העדכון בזמן אמת

במקום “לעדכן תוסף” כמו ב־Kodi, השרת מושך מחדש את `channels.json` כל N שניות.

כלומר:
- אם הערוצים/לינקים משתנים ב־GitHub, השרת קולט את זה לבד
- כתובת ה־manifest נשארת קבועה
- המשתמש לא צריך להתקין מחדש כל עוד מבנה ה־manifest לא השתנה

## דברים שצריך לדעת

1. זה שלב ראשון שממפה את ה־Live / Radio, כי זה החלק שהכי מתאים לסטרימיו.
2. חלק מהמקורות תלויים בדפי ביניים, tokens או headers, ולכן יש resolvers פר־מודול.
3. תכונות UI של Kodi לא עוברות 1:1 לסטרימיו.
4. אם לקוח Stremio מסוים לא אוהב מקור מסוים, לרוב צריך לכוון שם `behaviorHints` או להוסיף proxy חיצוני.

## הצעד הבא

הצעד המתבקש אחרי זה הוא להוסיף:
- VOD של כאן / קשת / רשת / 14 / i24 / ספורט
- חיפוש
- EPG כתיאור חי לצד ערוצים
- קטלוגים נפרדים לפודקאסטים ולתכניות רדיו
