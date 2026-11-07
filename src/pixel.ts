import * as THREE from "three";

/** Paleta própria de cidade em pixel art (sRGB): telhas, vegetação, asfalto, concreto, céu. */
export const CITY_PALETTE = [
  "#22262b", "#3c434d", "#4a5560", "#5e6670", "#8a929a", "#b9bec3", "#e4e2da", // escuros a claros neutros
  "#7d3326", "#a8472f", "#c8603f", "#de8a62",                                // telhas cerâmicas
  "#2c5e26", "#3f8a32", "#68b043",                                           // vegetação
  "#a8906a", "#d8c9a8",                                                      // terra, bege
  "#4f7fb0", "#cfe3f3",                                                      // água/vidro, céu
];

export type PixelOpts = {
  px: number;
  levels: number;
  outline: boolean;
  mode: "palette" | "channels";
  saturation: number;
  dither: boolean;
};

/**
 * Modo pixel: renderiza num alvo de baixa resolução e amplia com filtro nearest.
 * Pós-processo em sRGB: saturação, paleta fixa (cor mais próxima, com dithering ordenado opcional)
 * ou redução por canal, e contorno por descontinuidade de profundidade (perspectiva ou ortográfica).
 */
export class PixelPass {
  private target: THREE.WebGLRenderTarget;
  private quad: THREE.Mesh;
  private ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private post = new THREE.Scene();
  private mat: THREE.ShaderMaterial;

  constructor() {
    this.target = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      depthTexture: new THREE.DepthTexture(1, 1),
    });
    const pal = CITY_PALETTE.map((h) => new THREE.Color(h)).map((c) => {
      // THREE.Color guarda em linear; o shader compara em sRGB
      const s = c.clone().convertLinearToSRGB();
      return new THREE.Vector3(s.r, s.g, s.b);
    });
    this.mat = new THREE.ShaderMaterial({
      defines: { PAL_N: pal.length },
      uniforms: {
        tColor: { value: this.target.texture },
        tDepth: { value: this.target.depthTexture },
        texel: { value: new THREE.Vector2() },
        levels: { value: 6 },
        outline: { value: 1 },
        mode: { value: 0 },
        saturation: { value: 1.3 },
        dither: { value: 1 },
        isOrtho: { value: 0 },
        near: { value: 0.5 },
        far: { value: 3000 },
        pal: { value: pal },
      },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D tColor;
        uniform sampler2D tDepth;
        uniform vec2 texel;
        uniform float levels, outline, mode, saturation, dither, isOrtho, near, far;
        uniform vec3 pal[PAL_N];
        varying vec2 vUv;
        float lin(vec2 uv) {
          float z = texture2D(tDepth, uv).x;
          if (isOrtho > 0.5) return near + z * (far - near) + 1000.0; // profundidade já é linear
          z = z * 2.0 - 1.0;
          return 2.0 * near * far / (far + near - z * (far - near));
        }
        float bayer(vec2 p) {
          int x = int(mod(p.x, 4.0)), y = int(mod(p.y, 4.0));
          int i = x + y * 4;
          float m[16];
          m[0]=0.;m[1]=8.;m[2]=2.;m[3]=10.;m[4]=12.;m[5]=4.;m[6]=14.;m[7]=6.;
          m[8]=3.;m[9]=11.;m[10]=1.;m[11]=9.;m[12]=15.;m[13]=7.;m[14]=13.;m[15]=5.;
          for (int k = 0; k < 16; k++) if (k == i) return m[k] / 16.0 - 0.5;
          return 0.0;
        }
        void main() {
          vec3 c = texture2D(tColor, vUv).rgb;
          c = pow(max(c, 0.0), vec3(1.0 / 2.2));                  // linear -> sRGB
          float l = dot(c, vec3(0.299, 0.587, 0.114));
          c = clamp(mix(vec3(l), c, saturation), 0.0, 1.0);        // saturação
          vec2 cell = vUv / texel;
          if (dither > 0.5) c += bayer(cell) * (mode < 0.5 ? 0.05 : 0.5 / levels);
          if (mode < 0.5) {
            vec3 best = pal[0]; float bd = 1e9;
            for (int i = 0; i < PAL_N; i++) {
              vec3 d = (c - pal[i]) * vec3(0.30, 0.59, 0.11) * 3.0;
              float e = dot(d, d) + 0.25 * dot(c - pal[i], c - pal[i]);
              if (e < bd) { bd = e; best = pal[i]; }
            }
            c = best;
          } else {
            c = floor(clamp(c, 0.0, 1.0) * levels + 0.5) / levels;
          }
          if (outline > 0.5) {
            float d = lin(vUv);
            float m = max(max(lin(vUv + vec2(texel.x, 0.0)), lin(vUv - vec2(texel.x, 0.0))),
                          max(lin(vUv + vec2(0.0, texel.y)), lin(vUv - vec2(0.0, texel.y))));
            if ((m - d) / d > (isOrtho > 0.5 ? 0.0015 : 0.06)) c *= 0.45;
          }
          gl_FragColor = vec4(c, 1.0);
        }`,
      depthTest: false,
      depthWrite: false,
    });
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.mat);
    this.post.add(this.quad);
  }

  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene,
         camera: THREE.PerspectiveCamera | THREE.OrthographicCamera, opts: PixelOpts) {
    const size = renderer.getSize(new THREE.Vector2());
    const w = Math.max(1, Math.floor(size.x / opts.px));
    const h = Math.max(1, Math.floor(size.y / opts.px));
    if (this.target.width !== w || this.target.height !== h) this.target.setSize(w, h);
    const u = this.mat.uniforms;
    u.texel.value.set(1 / w, 1 / h);
    u.levels.value = opts.levels;
    u.outline.value = opts.outline ? 1 : 0;
    u.mode.value = opts.mode === "palette" ? 0 : 1;
    u.saturation.value = opts.saturation;
    u.dither.value = opts.dither ? 1 : 0;
    u.isOrtho.value = (camera as THREE.OrthographicCamera).isOrthographicCamera ? 1 : 0;
    u.near.value = camera.near;
    u.far.value = camera.far;
    renderer.setRenderTarget(this.target);
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    renderer.render(this.post, this.ortho);
  }

  dispose() {
    this.target.dispose();
    this.mat.dispose();
    this.quad.geometry.dispose();
  }
}
