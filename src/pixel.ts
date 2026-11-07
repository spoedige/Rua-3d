import * as THREE from "three";

/**
 * Modo pixel: renderiza a cena num alvo de baixa resolução e amplia com filtro nearest.
 * Pós-processo: redução de cores por canal (em sRGB) e contorno opcional por descontinuidade de profundidade.
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
    this.mat = new THREE.ShaderMaterial({
      uniforms: {
        tColor: { value: this.target.texture },
        tDepth: { value: this.target.depthTexture },
        texel: { value: new THREE.Vector2() },
        levels: { value: 6 },
        outline: { value: 1 },
        near: { value: 0.5 },
        far: { value: 3000 },
      },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D tColor;
        uniform sampler2D tDepth;
        uniform vec2 texel;
        uniform float levels;
        uniform float outline;
        uniform float near;
        uniform float far;
        varying vec2 vUv;
        float lin(vec2 uv) {
          float z = texture2D(tDepth, uv).x * 2.0 - 1.0;
          return 2.0 * near * far / (far + near - z * (far - near));
        }
        void main() {
          vec3 c = texture2D(tColor, vUv).rgb;
          c = pow(max(c, 0.0), vec3(1.0 / 2.2));           // linear -> sRGB
          c = floor(c * levels + 0.5) / levels;            // paleta reduzida
          if (outline > 0.5) {
            float d = lin(vUv);
            float m = max(max(lin(vUv + vec2(texel.x, 0.0)), lin(vUv - vec2(texel.x, 0.0))),
                          max(lin(vUv + vec2(0.0, texel.y)), lin(vUv - vec2(0.0, texel.y))));
            if ((m - d) / d > 0.06) c *= 0.35;               // borda de objeto mais próximo
          }
          gl_FragColor = vec4(c, 1.0);
        }`,
      depthTest: false,
      depthWrite: false,
    });
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.mat);
    this.post.add(this.quad);
  }

  /** px = tamanho do pixel em pixels de tela */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera,
         opts: { px: number; levels: number; outline: boolean }) {
    const size = renderer.getSize(new THREE.Vector2());
    const w = Math.max(1, Math.floor(size.x / opts.px));
    const h = Math.max(1, Math.floor(size.y / opts.px));
    if (this.target.width !== w || this.target.height !== h) this.target.setSize(w, h);
    const u = this.mat.uniforms;
    u.texel.value.set(1 / w, 1 / h);
    u.levels.value = opts.levels;
    u.outline.value = opts.outline ? 1 : 0;
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
