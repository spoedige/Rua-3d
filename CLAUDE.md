# Rua 3D

Modelo 3D de um trecho de rua em São Paulo/SP para estudar uma intervenção arquitetônica.

## Área
- Eixo: início -23.635124, -46.632498 / fim -23.633756, -46.632802 (~155 m)
- Faixa: 30 m para cada lado do eixo (buffer calculado em UTM 23S, EPSG:31983)
- Bbox de download: SO -23.635394, -46.633096 / NE -23.633486, -46.632204

## Stack
- Front: React + TypeScript + Vite, MapLibre GL (base OpenFreeMap), three.js
- Dados: Python (shapely, pyproj, numpy, pillow, requests, mapbox-vector-tile, laspy[lazrs], scipy)
- Sem PMTiles: GeoJSON/JSON estáticos em `public/data/`

## Estrutura
```
scripts/fetch_data.py   gera os dados
scripts/plot_check.py   PNG de conferência em docs/conferencia.png
public/data/            area.geojson, streets.geojson, buildings.geojson, terrain.json
src/                    app React (etapa 2: App.tsx, mapa MapLibre)
```

## Fontes de dados
- Vias e edificações: OSM via vector tiles do OpenFreeMap (z14, camadas transportation, transportation_name, building). Overpass não acessível do ambiente cloud
- Edificações: GeoSampa WFS `geoportal:edificacao` (aerofotogrametria 1:5000) > OSM > Microsoft Footprints, sem sobreposição > 30%
- Altura: `heights_manual.csv` (opcional, id,pavimentos,altura_m) > LiDAR 2017 PMSP (LAZ em s3 laz-m3dc-pmsp, folhas via WFS `quadricula_folha_mds`, cache em `data_cache/`): mediana de MDS - MDT no footprint (`height`), p20 (`height_eave`) e p90 (`height_ridge`) > `qt_altura_edificacao` do GeoSampa > tiles OSM > 6 m. Origem em `height_source`
- Terreno: AWS Terrain Tiles (Terrarium), z15 (~4,4 m/px nesta latitude); na 3a passa a vir do LiDAR
- Ortofoto: GeoSampa 2017/2020 por folha (índice WFS `quadricula_orto_2017_E1000`); o WMS só serve a articulação, URL de download a confirmar
- Tiles não trazem width/lanes das vias (campos nulos)
- Exclusões por id (`EXCLUDE_IDS` em fetch_data.py): `ms/13` (Microsoft, 1.134 m², funde várias construções, quase todo fora da faixa). IDs `ms/N` seguem a ordem do arquivo da Microsoft

## Etapas
1. Dados: `pip install shapely pyproj numpy pillow requests mapbox-vector-tile 'laspy[lazrs]' scipy matplotlib && python scripts/fetch_data.py && python scripts/plot_check.py`
2. Mapa MapLibre com os dados e a faixa destacada (feito). `npm i && npm run dev`; deploy: `npm run build` e publicar `dist/` na branch `gh-pages` (https://spoedige.github.io/rua-3d/)
3a. Cena three.js: terreno LiDAR 0,5 m (MDT, classe 2) recortado na bbox; prédios com telhado de duas águas (beiral `height_eave`, cumeeira `height_ridge`) e fachadas neutras; ortofoto GeoSampa no chão e no topo dos telhados (fallback: cor RGB dos pontos LiDAR `MDS_color`); câmera orbital. Peso dos assets publicados < 15 MB
3b. Árvores (classe 5) e calçadas/meio-fio do LiDAR; nuvem colorida como camada opcional; troca de ids por id estável derivado do centroide (fonte + E/N UTM arredondados), migrando `EXCLUDE_IDS` e `heights_manual.csv`
4. Camada de proposta: importar glTF ou desenhar volumes, alternando existente/proposta
