# Rua 3D

Modelo 3D de um trecho de rua em São Paulo/SP para estudar uma intervenção arquitetônica.

## Área
- Eixo: início -23.635124, -46.632498 / fim -23.633756, -46.632802 (~155 m)
- Faixa: 30 m para cada lado do eixo (buffer calculado em UTM 23S, EPSG:31983)
- Bbox de download: SO -23.635394, -46.633096 / NE -23.633486, -46.632204

## Stack
- Front: React + TypeScript + Vite, MapLibre GL (base OpenFreeMap), three.js
- Dados: Python (shapely, pyproj, numpy, pillow, requests)
- Sem PMTiles: GeoJSON/JSON estáticos em `public/data/`

## Estrutura
```
scripts/fetch_data.py   gera os dados
public/data/            area.geojson, streets.geojson, buildings.geojson, terrain.json
src/                    app (etapas 2 a 4)
```

## Fontes de dados
- Vias e edificações: OpenStreetMap via Overpass API
- Edificações faltantes: Microsoft Global Building Footprints
- Terreno: AWS Terrain Tiles (Terrarium), z15 (~4,4 m/px nesta latitude)
- Altura: `height` > `building:levels`*3 m > 6 m padrão; origem em `height_source`

## Etapas
1. Dados: `pip install shapely pyproj numpy pillow requests && python scripts/fetch_data.py`
2. Mapa MapLibre com os dados e a faixa destacada
3. Cena three.js com terreno, vias e prédios extrudados, câmera orbital
4. Camada de proposta: importar glTF ou desenhar volumes, alternando existente/proposta
