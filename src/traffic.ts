import * as THREE from "three";

type Lane = { name: string | null; pts: number[]; oneway: boolean; hw: number };
type Path = { p: THREE.Vector3[]; s: number[]; len: number };
type Agent = { obj: THREE.Object3D; path: Path; s: number; v: number; dir: 1 | -1; bounce: boolean };

const CAR_COLORS = ["#d9d9d9", "#f2f2f2", "#2b2b2b", "#8c8c8c", "#b22222", "#1f4e8c", "#c0c0c0", "#5a6b3c"];
const PED_COLORS = ["#3b5b92", "#a83232", "#2f6b3a", "#6b4f8a", "#c9a227", "#444444"];

// local (x leste, y norte, z cota) -> three
function toPath(pts: number[], zRef: number): Path {
  const p: THREE.Vector3[] = [];
  for (let i = 0; i < pts.length; i += 3) p.push(new THREE.Vector3(pts[i], pts[i + 2] - zRef, -pts[i + 1]));
  const s = [0];
  for (let i = 1; i < p.length; i++) s.push(s[i - 1] + p[i].distanceTo(p[i - 1]));
  return { p, s, len: s[s.length - 1] };
}

function at(path: Path, s: number, pos: THREE.Vector3, ahead: THREE.Vector3) {
  const { p, s: cs } = path;
  let i = 1;
  while (i < cs.length - 1 && cs[i] < s) i++;
  const t = (s - cs[i - 1]) / Math.max(1e-6, cs[i] - cs[i - 1]);
  pos.lerpVectors(p[i - 1], p[i], Math.min(1, Math.max(0, t)));
  ahead.subVectors(p[i], p[i - 1]).normalize();
}

function carMesh(color: string) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.6, 4.0),
    new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.2, flatShading: true }));
  body.position.y = 0.55;
  const cabin = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.5, 2.0),
    new THREE.MeshStandardMaterial({ color: "#1d2733", roughness: 0.2, metalness: 0.4, flatShading: true }));
  cabin.position.set(0, 1.08, -0.2);
  const wheels = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.45, 3.0),
    new THREE.MeshStandardMaterial({ color: "#111111", roughness: 1 }));
  wheels.position.y = 0.25;
  g.add(body, cabin, wheels);
  g.traverse((o) => { if ((o as THREE.Mesh).isMesh) o.castShadow = true; });
  return g;
}

function pedMesh(color: string) {
  const g = new THREE.Group();
  const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.18, 1.1, 6),
    new THREE.MeshStandardMaterial({ color, flatShading: true }));
  torso.position.y = 0.95;
  const legs = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.12, 0.45, 6),
    new THREE.MeshStandardMaterial({ color: "#2a2a35", flatShading: true }));
  legs.position.y = 0.22;
  const head = new THREE.Mesh(new THREE.IcosahedronGeometry(0.14, 0),
    new THREE.MeshStandardMaterial({ color: "#c8a07a", flatShading: true }));
  head.position.y = 1.65;
  g.add(torso, legs, head);
  g.traverse((o) => { if ((o as THREE.Mesh).isMesh) o.castShadow = true; });
  return g;
}

export class Traffic {
  group = new THREE.Group();
  private lanes: Path[];
  private walks: Path[];
  private cars: Agent[] = [];
  private peds: Agent[] = [];
  private tmpP = new THREE.Vector3();
  private tmpD = new THREE.Vector3();

  constructor(data: { lanes: Lane[]; walks: number[][] }, zRef: number) {
    this.lanes = data.lanes.map((l) => toPath(l.pts, zRef)).filter((p) => p.len > 10);
    this.walks = data.walks.map((w) => toPath(w, zRef)).filter((p) => p.len > 8);
  }

  /** densidade em carros por 100 m de faixa */
  setCars(density: number) {
    for (const c of this.cars) this.group.remove(c.obj);
    this.cars = [];
    let k = 0;
    for (const path of this.lanes) {
      const n = Math.round((path.len / 100) * density);
      const v = 6 + Math.random() * 3; // 22-32 km/h: rua estreita, todos na mesma velocidade por faixa
      for (let i = 0; i < n; i++) {
        const obj = carMesh(CAR_COLORS[k++ % CAR_COLORS.length]);
        this.group.add(obj);
        this.cars.push({ obj, path, s: (i / n) * path.len + Math.random() * 2, v, dir: 1, bounce: false });
      }
    }
    this.update(0);
  }

  setPeds(on: boolean, perPath = 3) {
    for (const p of this.peds) this.group.remove(p.obj);
    this.peds = [];
    if (!on) return;
    let k = 0;
    for (const path of this.walks) {
      const n = Math.max(1, Math.round((path.len / 60) * perPath));
      for (let i = 0; i < n; i++) {
        const obj = pedMesh(PED_COLORS[k++ % PED_COLORS.length]);
        this.group.add(obj);
        this.peds.push({ obj, path, s: Math.random() * path.len, v: 1.1 + Math.random() * 0.5,
          dir: Math.random() < 0.5 ? 1 : -1, bounce: true });
      }
    }
    this.update(0);
  }

  update(dt: number) {
    for (const a of [...this.cars, ...this.peds]) {
      a.s += a.v * a.dir * dt;
      if (a.bounce) {
        if (a.s > a.path.len) { a.s = a.path.len; a.dir = -1; }
        if (a.s < 0) { a.s = 0; a.dir = 1; }
      } else if (a.s > a.path.len) {
        a.s -= a.path.len; // reaparece no início da faixa (borda da cena)
      }
      at(a.path, a.s, this.tmpP, this.tmpD);
      if (a.dir < 0) this.tmpD.negate();
      a.obj.position.copy(this.tmpP);
      a.obj.lookAt(this.tmpP.x + this.tmpD.x, this.tmpP.y + this.tmpD.y, this.tmpP.z + this.tmpD.z);
    }
  }

  get counts() {
    return { cars: this.cars.length, peds: this.peds.length };
  }
}
