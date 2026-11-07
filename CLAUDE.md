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
src/                    app (etapas 2 a 4)
```

## Fontes de dados
- Vias e edificações: OSM via vector tiles do OpenFreeMap (z14, camadas transportation, transportation_name, building). Overpass não acessível do ambiente cloud
- Edificações: GeoSampa WFS `geoportal:edificacao` (aerofotogrametria 1:5000) > OSM > Microsoft Footprints, sem sobreposição > 30%
- Altura: `heights_manual.csv` (opcional, id,pavimentos,altura_m) > LiDAR 2017 PMSP (LAZ em s3 laz-m3dc-pmsp, folhas via WFS `quadricula_folha_mds`, cache em `data_cache/`): mediana de MDS - MDT no footprint (`height`), p20 (`height_eave`) e p90 (`height_ridge`) > `qt_altura_edificacao` do GeoSampa > tiles OSM > 6 m. Origem em `height_source`
- Terreno: AWS Terrain Tiles (Terrarium), z15 (~4,4 m/px nesta latitude)
- Tiles não trazem width/lanes das vias (campos nulos)

## Etapas
1. Dados: `pip install shapely pyproj numpy pillow requests mapbox-vector-tile 'laspy[lazrs]' scipy matplotlib && python scripts/fetch_data.py && python scripts/plot_check.py`
2. Mapa MapLibre com os dados e a faixa destacada
3. Cena three.js com terreno, vias e prédios extrudados, câmera orbital
4. Camada de proposta: importar glTF ou desenhar volumes, alternando existente/proposta
