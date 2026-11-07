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
scripts/build_scene.py  assets da cena 3D em public/data/scene/ (MDT, textura, prédios, linhas)
public/data/            area.geojson, streets.geojson, buildings.geojson, terrain.json
src/                    App.tsx (rotas #/ e #/3d), MapView.tsx (etapa 2), Scene3D.tsx (etapa 3a, three.js)
```

## Fontes de dados
- Vias e edificações: OSM via vector tiles do OpenFreeMap (z14, camadas transportation, transportation_name, building). Overpass não acessível do ambiente cloud
- Edificações: GeoSampa WFS `geoportal:edificacao` (aerofotogrametria 1:5000) > OSM > Microsoft Footprints, sem sobreposição > 30%
- Altura: `heights_manual.csv` (opcional, id,pavimentos,altura_m) > LiDAR 2017 PMSP (LAZ em s3 laz-m3dc-pmsp, folhas via WFS `quadricula_folha_mds`, cache em `data_cache/`): mediana de MDS - MDT no footprint (`height`), p20 (`height_eave`) e p90 (`height_ridge`) > `qt_altura_edificacao` do GeoSampa > tiles OSM > 6 m. Origem em `height_source`
- Terreno: AWS Terrain Tiles (Terrarium), z15 (~4,4 m/px nesta latitude); na 3a passa a vir do LiDAR
- Ortofoto: GeoSampa 2017/2020 só pelo portal interativo; WMS serve só a articulação e os padrões de `downloadArquivo.aspx` testados devolvem HTML. Textura usa a cor RGB do LiDAR (ponto mais alto por célula, 0,25 m)
- Tiles não trazem width/lanes das vias (campos nulos)
- IDs dos prédios: `fonte/E_N` (centroide UTM arredondado a 1 m, ex. `gs/333438_7385345`); id original da fonte em `src_id`. Ids antigos em `EXCLUDE_IDS` ou `heights_manual.csv` ainda funcionam, com aviso
- Exclusões por id (`EXCLUDE_IDS` em fetch_data.py): `ms/333509_7385277` (antigo ms/13; Microsoft, 1.134 m², funde várias construções, quase todo fora da faixa)
- Calçadas: GeoSampa WFS `geoportal:calcada` (oficial, com largura); meio-fio medido no MDT contra o lado da rua (fora de `quadra_viaria_editada`), mediana 0,16 m
- Árvores: picos do modelo de altura da classe 5 do LiDAR (36, 3,7-16 m). Cadastro `geoportal:arvore` tem só 8 pontos sem atributos, usado só para conferência

## Etapas
1. Dados: `pip install shapely pyproj numpy pillow requests mapbox-vector-tile 'laspy[lazrs]' scipy matplotlib && python scripts/fetch_data.py && python scripts/plot_check.py && python scripts/build_scene.py`
2. Mapa MapLibre com os dados e a faixa destacada (feito). `npm i && npm run dev`; deploy: `npm run build` e publicar `dist/` na branch `gh-pages` (https://spoedige.github.io/Rua-3d/ (maiúscula; o caminho diferencia))
3a. (feito) Cena three.js em `#/3d`; `python scripts/build_scene.py` gera os assets. Escopo: terreno LiDAR 0,5 m (MDT, classe 2) recortado na bbox; prédios com telhado de duas águas (beiral `height_eave`, cumeeira `height_ridge`) e fachadas neutras; ortofoto GeoSampa no chão e no topo dos telhados (fallback: cor RGB dos pontos LiDAR `MDS_color`); câmera orbital. Peso dos assets publicados < 15 MB
3b. (feito) Camadas de árvores, calçadas e nuvem (`points.bin`, 3,7 MB, carregada sob demanda); assets da cena 4,9 MB. Escopo: Árvores (classe 5) e calçadas/meio-fio do LiDAR; nuvem colorida como camada opcional; troca de ids por id estável derivado do centroide (fonte + E/N UTM arredondados), migrando `EXCLUDE_IDS` e `heights_manual.csv`
4. Camada de proposta: importar glTF ou desenhar volumes, alternando existente/proposta
