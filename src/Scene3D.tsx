import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { HEIGHT_SOURCES } from "./MapView";

const DATA = `${import.meta.env.BASE_URL}data/scene/`;

type Meta = {
  size: [number, number];
  points: { file: string; count: number };
  dtm: { res: number; nx: number; ny: number; min: number; max: number };
  ortho: { file: string; source: string };
};
type Info = {
  id: string; height_source: string; height: number;
  eave: number | null; ridge: number | null;
  walls: [number, number]; roof: [number, number];
};
type Picked = Info | null;

// local (x leste, y norte, z cota) -> three (x, y para cima, -z norte)
function toThree(arr: number[], zRef: number) {
  const out = new Float32Array(arr.length);
  for (let i = 0; i < arr.length; i += 3) {
    out[i] = arr[i];
    out[i + 1] = arr[i + 2] - zRef;
    out[i + 2] = -arr[i + 1];
  }
  return out;
}

export default function Scene3D() {
  const ref = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<Picked>(null);
  const [status, setStatus] = useState("Carregando dados…");
  const [texOnRoofs, setTexOnRoofs] = useState(true);
  const [layers, setLayers] = useState({ trees: true, sidewalks: true, cloud: false, model: true });
  const groups = useRef<Record<string, THREE.Object3D>>({});
  const loadCloud = useRef<() => Promise<void>>();
  const roofMat = useRef<THREE.MeshStandardMaterial>();
  const roofTex = useRef<THREE.Texture>();

  useEffect(() => {
    const el = ref.current!;
    let disposed = false;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#cfe3f3");
    const camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.5, 3000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.minDistance = 5;
    controls.maxDistance = 600;

    scene.add(new THREE.HemisphereLight("#ffffff", "#8a7f70", 1.4));
    const sun = new THREE.DirectionalLight("#fff4e0", 2.2);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.08;
    scene.add(sun, sun.target);

    const pickables: THREE.Mesh[] = [];
    let infos: Info[] = [];

    (async () => {
      const [meta, dtmBuf, bld, lines, tex] = await Promise.all([
        fetch(`${DATA}meta.json`).then((r) => r.json() as Promise<Meta>),
        fetch(`${DATA}dtm.bin`).then((r) => r.arrayBuffer()),
        fetch(`${DATA}buildings.json`).then((r) => r.json()),
        fetch(`${DATA}lines.json`).then((r) => r.json()),
        new THREE.TextureLoader().loadAsync(`${DATA}ortho.jpg`),
      ]);
      if (disposed) return;
      tex.colorSpace = THREE.SRGBColorSpace;
      tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
      roofTex.current = tex;
      const [W, H] = meta.size;
      const { nx, ny, res, min: zRef } = meta.dtm;
      const dtm = new Float32Array(dtmBuf);

      // terreno: linhas do MDT de sul para norte; linhas do plano de norte para sul
      const tg = new THREE.PlaneGeometry((nx - 1) * res, (ny - 1) * res, nx - 1, ny - 1);
      tg.rotateX(-Math.PI / 2);
      tg.translate(((nx - 1) * res) / 2, 0, -((ny - 1) * res) / 2);
      const pos = tg.attributes.position as THREE.BufferAttribute;
      for (let r = 0; r < ny; r++)
        for (let c = 0; c < nx; c++) pos.setY(r * nx + c, dtm[(ny - 1 - r) * nx + c] - zRef);
      tg.computeVertexNormals();
      const ground = new THREE.Mesh(tg, new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95 }));
      ground.receiveShadow = true;
      scene.add(ground);

      // prédios
      infos = bld.info;
      const wallGeo = new THREE.BufferGeometry();
      wallGeo.setAttribute("position", new THREE.BufferAttribute(toThree(bld.walls, zRef), 3));
      wallGeo.computeVertexNormals();
      const walls = new THREE.Mesh(wallGeo, new THREE.MeshStandardMaterial({
        color: "#e9e4da", roughness: 0.9, side: THREE.DoubleSide,
      }));
      walls.castShadow = walls.receiveShadow = true;
      walls.userData.kind = "walls";

      const roofPos = toThree(bld.roofs, zRef);
      const uv = new Float32Array((roofPos.length / 3) * 2);
      for (let i = 0, j = 0; i < roofPos.length; i += 3, j += 2) {
        uv[j] = roofPos[i] / W;
        uv[j + 1] = -roofPos[i + 2] / H;
      }
      const roofGeo = new THREE.BufferGeometry();
      roofGeo.setAttribute("position", new THREE.BufferAttribute(roofPos, 3));
      roofGeo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      roofGeo.computeVertexNormals();
      roofMat.current = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8, side: THREE.DoubleSide });
      const roofs = new THREE.Mesh(roofGeo, roofMat.current);
      roofs.castShadow = roofs.receiveShadow = true;
      roofs.userData.kind = "roof";
      scene.add(walls, roofs);
      pickables.push(walls, roofs);

      // vias e faixa, levemente acima do chão
      const addLine = (pts: number[], color: string, lift: number) => {
        const p = toThree(pts, zRef - lift);
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(p, 3));
        scene.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color, depthTest: true })));
      };
      for (const s of lines.streets) addLine(s.pts, "#e53e3e", 0.25);
      for (const a of lines.area) addLine(a, "#f6ad1a", 0.3);

      // modelo existente (prédios + calçadas) agrupado para poder esconder ao ver a nuvem
      const model = new THREE.Group();
      scene.remove(walls, roofs);
      model.add(walls, roofs, ground);
      scene.add(model);
      groups.current.model = model;

      // calçadas (GeoSampa) com face de meio-fio
      const sw = await fetch(`${DATA}sidewalks.json`).then((r) => r.json());
      const swGroup = new THREE.Group();
      const swMat = new THREE.MeshStandardMaterial({ color: "#d9d6cf", roughness: 1, side: THREE.DoubleSide });
      for (const key of ["top", "side"] as const) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(toThree(sw[key], zRef), 3));
        g.computeVertexNormals();
        const m = new THREE.Mesh(g, swMat);
        m.receiveShadow = true;
        swGroup.add(m);
      }
      model.add(swGroup);
      groups.current.sidewalks = swGroup;

      // árvores (LiDAR classe 5): tronco + copa instanciados
      const trees: { x: number; y: number; z: number; h: number; r: number }[] =
        await fetch(`${DATA}trees.json`).then((r) => r.json());
      const trunk = new THREE.InstancedMesh(
        new THREE.CylinderGeometry(0.15, 0.22, 1, 6).translate(0, 0.5, 0),
        new THREE.MeshStandardMaterial({ color: "#6b4f3a", roughness: 1 }), trees.length);
      const crown = new THREE.InstancedMesh(
        new THREE.IcosahedronGeometry(1, 1),
        new THREE.MeshStandardMaterial({ color: "#4f7d3a", roughness: 0.9, flatShading: true }), trees.length);
      const mtx = new THREE.Matrix4();
      trees.forEach((t, i) => {
        const base = t.z - zRef;
        const cr = Math.min(t.r, t.h * 0.45);
        const crownH = Math.max(cr * 1.6, t.h * 0.55);
        const trunkH = t.h - crownH * 0.75;
        mtx.compose(new THREE.Vector3(t.x, base, -t.y), new THREE.Quaternion(), new THREE.Vector3(1, Math.max(trunkH, 1), 1));
        trunk.setMatrixAt(i, mtx);
        mtx.compose(new THREE.Vector3(t.x, base + t.h - crownH / 2, -t.y), new THREE.Quaternion(),
          new THREE.Vector3(cr, crownH / 2, cr));
        crown.setMatrixAt(i, mtx);
      });
      trunk.castShadow = crown.castShadow = true;
      const treeGroup = new THREE.Group();
      treeGroup.add(trunk, crown);
      scene.add(treeGroup);
      groups.current.trees = treeGroup;

      // nuvem LiDAR colorida: carregada só quando ativada
      loadCloud.current = async () => {
        if (groups.current.cloud) return;
        const buf = await fetch(`${DATA}${meta.points.file}`).then((r) => r.arrayBuffer());
        const n = meta.points.count;
        const p16 = new Int16Array(buf, 0, n * 3);
        const rgb = new Uint8Array(buf, n * 6, n * 3);
        const pos = new Float32Array(n * 3);
        const col = new Float32Array(n * 3);
        const lut = Array.from({ length: 256 }, (_, v) => new THREE.Color().setRGB(v / 255, 0, 0, THREE.SRGBColorSpace).r);
        for (let i = 0; i < n * 3; i++) col[i] = lut[rgb[i]];
        for (let i = 0; i < n; i++) {
          pos[i * 3] = p16[i * 3] / 100;
          pos[i * 3 + 1] = p16[i * 3 + 2] / 100;
          pos[i * 3 + 2] = -p16[i * 3 + 1] / 100;
        }
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
        g.setAttribute("color", new THREE.BufferAttribute(col, 3));
        const cloud = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.5, vertexColors: true }));
        scene.add(cloud);
        groups.current.cloud = cloud;
      };

      // câmera e sol
      const cx = W / 2, cz = -H / 2;
      controls.target.set(cx, 4, cz);
      camera.position.set(cx - 90, 110, cz + 140);
      sun.position.set(cx + 80, 160, cz + 60);
      sun.target.position.set(cx, 0, cz);
      const s = Math.max(W, H) * 0.7;
      Object.assign(sun.shadow.camera, { left: -s, right: s, top: s, bottom: -s, near: 1, far: 500 });
      sun.shadow.camera.updateProjectionMatrix();
      setStatus("");
    })().catch((e) => setStatus(`Erro ao carregar: ${e}`));

    // seleção por clique (sem arrastar)
    const ray = new THREE.Raycaster();
    let down = { x: 0, y: 0 };
    const onDown = (e: PointerEvent) => (down = { x: e.clientX, y: e.clientY });
    const onUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
      const r = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
      const hit = ray.intersectObjects(pickables).find((h) => h.object.visible && h.object.parent?.visible);
      if (!hit || hit.faceIndex == null) return setPicked(null);
      const v = hit.faceIndex * 3;
      const key = hit.object.userData.kind === "roof" ? "roof" : "walls";
      setPicked(infos.find((b) => v >= b[key][0] && v < b[key][0] + b[key][1]) ?? null);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    const onResize = () => {
      camera.aspect = el.clientWidth / el.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(el.clientWidth, el.clientHeight);
    };
    window.addEventListener("resize", onResize);
    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });

    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      window.removeEventListener("resize", onResize);
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    const m = roofMat.current;
    if (!m) return;
    m.map = texOnRoofs ? roofTex.current ?? null : null;
    m.color.set(texOnRoofs ? "#ffffff" : "#b5651d");
    m.needsUpdate = true;
  }, [texOnRoofs, status]);

  useEffect(() => {
    const g = groups.current;
    if (g.trees) g.trees.visible = layers.trees;
    if (g.sidewalks) g.sidewalks.visible = layers.sidewalks;
    if (g.model) g.model.visible = layers.model;
    if (layers.cloud && !g.cloud && loadCloud.current) {
      setStatus("Carregando nuvem LiDAR…");
      loadCloud.current().then(() => setStatus("")).catch((e) => setStatus(`Erro na nuvem: ${e}`));
    }
    if (g.cloud) g.cloud.visible = layers.cloud;
  }, [layers, status]);

  const toggle = (k: keyof typeof layers) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setLayers((l) => ({ ...l, [k]: e.target.checked }));
  const fmt = (v: number | null) => (v == null ? "—" : `${v.toFixed(2)} m`);
  const label = (k: string) => HEIGHT_SOURCES.find(([s]) => s === k)?.[2] ?? k;

  return (
    <>
      <div id="map" ref={ref} />
      <div className="panel">
        <h1>Rua 3D · cena</h1>
        <small>
          Terreno e textura do LiDAR 2017 (0,5 m; cor do ponto mais alto). Telhados de duas águas entre beiral (p20) e
          cumeeira (p90). Arraste para girar, botão direito para mover, roda para zoom. Clique num prédio.
        </small>
        <label className="row" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={texOnRoofs} onChange={(e) => setTexOnRoofs(e.target.checked)} />
          Textura nos telhados
        </label>
        <label className="row">
          <input type="checkbox" checked={layers.model} onChange={toggle("model")} />
          Terreno, prédios e calçadas
        </label>
        <label className="row">
          <input type="checkbox" checked={layers.sidewalks} onChange={toggle("sidewalks")} />
          Calçadas (GeoSampa)
        </label>
        <label className="row">
          <input type="checkbox" checked={layers.trees} onChange={toggle("trees")} />
          Árvores (LiDAR)
        </label>
        <label className="row">
          <input type="checkbox" checked={layers.cloud} onChange={toggle("cloud")} />
          Nuvem LiDAR colorida (3,7 MB)
        </label>
        {status && <div style={{ marginTop: 6 }}>{status}</div>}
        {picked && (
          <div className="popup" style={{ marginTop: 8 }}>
            <table>
              <tbody>
                <tr><td>id</td><td><b>{picked.id}</b></td></tr>
                <tr><td>mediana</td><td><b>{fmt(picked.height)}</b></td></tr>
                <tr><td>beiral (p20)</td><td><b>{fmt(picked.eave)}</b></td></tr>
                <tr><td>cumeeira (p90)</td><td><b>{fmt(picked.ridge)}</b></td></tr>
                <tr><td>fonte</td><td><b>{label(picked.height_source)}</b></td></tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
