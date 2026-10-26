import type { BufferGeometry, Material, Mesh, Object3D, Texture } from 'three';

/** One asynchronous scene load owns these releases, including resources arriving after close. */
export class SceneResources {
  private closed = false;
  private releases: (() => void)[] = [];

  own(cleanup: () => void): () => void {
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      try { cleanup(); }
      catch (error) { console.warn('A scene resource could not be released:', error); }
    };
    if (this.closed) release();
    else this.releases.push(release);
    return release;
  }

  dispose(): void {
    if (this.closed) return;
    this.closed = true;
    while (this.releases.length) this.releases.pop()!();
  }
}

/** A loaded model can share materials, textures and geometry between several meshes. */
export function disposeMaterials(materials: Set<Material>): void {
  const textures = new Set<Texture>();
  for (const material of materials) {
    for (const value of Object.values(material)) {
      if (value && typeof value === 'object' && (value as Texture).isTexture) textures.add(value as Texture);
    }
  }
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
  materials.clear();
}

export function disposeObject(root: Object3D): void {
  const geometries = new Set<BufferGeometry>();
  const materials = new Set<Material>();
  root.traverse((object) => {
    const mesh = object as Mesh;
    if (!mesh.isMesh) return;
    geometries.add(mesh.geometry);
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) materials.add(material);
  });
  for (const geometry of geometries) geometry.dispose();
  disposeMaterials(materials);
}
