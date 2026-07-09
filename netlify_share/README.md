# ShadeMatch EDA — megosztható statikus oldal (Netlify)

Ez a mappa egy önálló, statikus weboldal: az `index.html` a ShadeMatch EDA v4
statisztikus-konzulensi változata, minden ábrával (plotly 3D, képek, CSS) **beágyazva** —
nincs külső függősége, bármely statikus tárhelyen működik.

## Megosztás Netlify-on

### A) Leggyorsabb — Netlify Drop (fiók nélkül is)
1. Nyisd meg: <https://app.netlify.com/drop>
2. Húzd rá **ezt a teljes `netlify_share` mappát** (vagy csak az `index.html`-t).
3. Kapsz egy azonnali `https://<véletlen-név>.netlify.app` linket, amit megoszthatsz.
   (Bejelentkezve átnevezheted az oldalt a Site settings → Change site name alatt.)

### B) Netlify CLI-vel
```bash
npm install -g netlify-cli      # egyszer
cd netlify_share
netlify deploy --dir . --prod   # követi a bejelentkezést, majd kiadja a linket
```

### C) Git-alapú folyamatos deploy
Ha ezt a repót Netlify-hoz kötöd, a `netlify.toml` `publish = "."` beállítása miatt
ebből a mappából (base = `netlify_share`) build nélkül szolgálja ki az `index.html`-t.

## Frissítés
Ha újrarendereled a forrást (`notes/ShadeMatch_EDA_v4_statisztikus.qmd`), másold felül az
`index.html`-t, és deployolj újra:
```bash
cp ../notes/ShadeMatch_EDA_v4_statisztikus.html index.html
```
