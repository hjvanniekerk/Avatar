const fs = require("fs");
const path = require("path");

function align4(value) {
  return (value + 3) & ~3;
}

function readGlb(file) {
  const data = fs.readFileSync(file);
  if (data.toString("ascii", 0, 4) !== "glTF") throw new Error("Not a GLB: " + file);
  let offset = 12;
  let json = null;
  let bin = null;
  while (offset < data.length) {
    const length = data.readUInt32LE(offset);
    const type = data.toString("ascii", offset + 4, offset + 8);
    const chunk = data.subarray(offset + 8, offset + 8 + length);
    if (type === "JSON") json = JSON.parse(chunk.toString("utf8").replace(/\0+$/g, "").trimEnd());
    if (type === "BIN\0") bin = Buffer.from(chunk);
    offset += 8 + length;
  }
  if (!json || !bin) throw new Error("GLB must contain JSON and BIN chunks: " + file);
  return { json, bin };
}

function writeGlb(json, bin, output) {
  json.buffers = [{ byteLength: bin.length }];
  let jsonBuffer = Buffer.from(JSON.stringify(json), "utf8");
  jsonBuffer = Buffer.concat([jsonBuffer, Buffer.alloc(align4(jsonBuffer.length) - jsonBuffer.length, 0x20)]);
  const paddedBin = Buffer.concat([bin, Buffer.alloc(align4(bin.length) - bin.length)]);
  const header = Buffer.alloc(12);
  header.write("glTF", 0, "ascii");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonBuffer.length + 8 + paddedBin.length, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonBuffer.length, 0);
  jsonHeader.write("JSON", 4, "ascii");
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(paddedBin.length, 0);
  binHeader.write("BIN\0", 4, "ascii");
  fs.writeFileSync(output, Buffer.concat([header, jsonHeader, jsonBuffer, binHeader, paddedBin]));
}

function componentCount(type) {
  return { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[type] || 1;
}

function componentSize(componentType) {
  return { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 }[componentType] || 4;
}

function accessorLayout(json, accessorIndex) {
  const accessor = json.accessors[accessorIndex];
  const view = json.bufferViews[accessor.bufferView];
  const components = componentCount(accessor.type);
  const size = componentSize(accessor.componentType);
  if (accessor.componentType !== 5126) throw new Error("Only FLOAT accessors can be transformed");
  return {
    accessor,
    components,
    stride: view.byteStride || components * size,
    byteOffset: (view.byteOffset || 0) + (accessor.byteOffset || 0),
  };
}

function readVec(bin, offset, components) {
  const out = [];
  for (let i = 0; i < components; i += 1) out.push(bin.readFloatLE(offset + i * 4));
  return out;
}

function writeVec(bin, offset, values, components) {
  for (let i = 0; i < components; i += 1) bin.writeFloatLE(values[i], offset + i * 4);
}

function deg(value) {
  return value * Math.PI / 180;
}

function smoothstep(edge0, edge1, value) {
  const x = Math.max(0, Math.min(1, (value - edge0) / (edge1 - edge0)));
  return x * x * (3 - 2 * x);
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function normalize(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

function rotateX(v, angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c];
}

function rotateY(v, angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c];
}

function rotateZ(v, angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return [v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2]];
}

function rotateHeadPoint(point, pose) {
  const pivot = pose.headPivot || [0, 1.405, 0.02];
  let p = [point[0] - pivot[0], point[1] - pivot[1], point[2] - pivot[2]];
  p = rotateX(p, deg(pose.headPitch || 0));
  p = rotateY(p, deg(pose.headYaw || 0));
  return [p[0] + pivot[0], p[1] + pivot[1], p[2] + pivot[2]];
}

function rotateHeadVector(vector, pose) {
  let v = rotateX(vector, deg(pose.headPitch || 0));
  v = rotateY(v, deg(pose.headYaw || 0));
  return normalize(v);
}

function armsOutWeight(point) {
  const absX = Math.abs(point[0]);
  const sideWeight = smoothstep(0.24, 0.34, absX);
  const lowerGate = smoothstep(0.76, 0.86, point[1]);
  const upperGate = 1 - smoothstep(1.07, 1.20, point[1]);
  return Math.max(0, Math.min(1, sideWeight * lowerGate * upperGate));
}

function rotateArmPoint(point, pose) {
  if (!pose.armsOut) return point;
  const side = point[0] >= 0 ? 1 : -1;
  const weight = armsOutWeight(point);
  if (weight <= 0) return point;
  const pivot = [side * 0.285, 1.085, 0.095];
  const angle = deg(side * 76);
  const local = [point[0] - pivot[0], point[1] - pivot[1], point[2] - pivot[2]];
  const rotated = rotateZ(local, angle);
  const target = [rotated[0] + pivot[0], rotated[1] + pivot[1], rotated[2] + pivot[2]];
  return [
    lerp(point[0], target[0], weight),
    lerp(point[1], target[1], weight),
    lerp(point[2], target[2], weight),
  ];
}

function rotateArmVector(vector, point, pose) {
  if (!pose.armsOut) return vector;
  const side = point[0] >= 0 ? 1 : -1;
  const weight = armsOutWeight(point);
  if (weight <= 0) return vector;
  const target = rotateZ(vector, deg(side * 76));
  return normalize([
    lerp(vector[0], target[0], weight),
    lerp(vector[1], target[1], weight),
    lerp(vector[2], target[2], weight),
  ]);
}

function transformAccessor(json, bin, accessorIndex, transform, minMaxComponents = 3) {
  const layout = accessorLayout(json, accessorIndex);
  const min = Array(minMaxComponents).fill(Infinity);
  const max = Array(minMaxComponents).fill(-Infinity);
  for (let i = 0; i < layout.accessor.count; i += 1) {
    const offset = layout.byteOffset + i * layout.stride;
    const value = readVec(bin, offset, layout.components);
    const next = transform(value, i);
    writeVec(bin, offset, next, layout.components);
    for (let c = 0; c < minMaxComponents; c += 1) {
      min[c] = Math.min(min[c], next[c]);
      max[c] = Math.max(max[c], next[c]);
    }
  }
  layout.accessor.min = min;
  layout.accessor.max = max;
}

function transformPrimitive(json, bin, primitive, pose, mode) {
  const positionAccessor = primitive.attributes && primitive.attributes.POSITION;
  if (positionAccessor == null) return;
  const originalPositions = [];
  const positionLayout = accessorLayout(json, positionAccessor);
  for (let i = 0; i < positionLayout.accessor.count; i += 1) {
    originalPositions.push(readVec(bin, positionLayout.byteOffset + i * positionLayout.stride, 3));
  }

  transformAccessor(json, bin, positionAccessor, (value, i) => {
    if (mode === "head") return rotateHeadPoint(value, pose);
    if (mode === "arms") return rotateArmPoint(value, pose);
    return value;
  });

  const normalAccessor = primitive.attributes.NORMAL;
  if (normalAccessor != null) {
    transformAccessor(json, bin, normalAccessor, (value, i) => {
      if (mode === "head") return rotateHeadVector(value, pose);
      if (mode === "arms") return rotateArmVector(value, originalPositions[i], pose);
      return value;
    });
  }

  const tangentAccessor = primitive.attributes.TANGENT;
  if (tangentAccessor != null) {
    const layout = accessorLayout(json, tangentAccessor);
    for (let i = 0; i < layout.accessor.count; i += 1) {
      const offset = layout.byteOffset + i * layout.stride;
      const value = readVec(bin, offset, layout.components);
      const vector = [value[0], value[1], value[2]];
      let next = vector;
      if (mode === "head") next = rotateHeadVector(vector, pose);
      if (mode === "arms") next = rotateArmVector(vector, originalPositions[i], pose);
      writeVec(bin, offset, [next[0], next[1], next[2], value[3] == null ? 1 : value[3]], layout.components);
    }
  }
}

function createPoseVariant(input, output, pose) {
  const read = readGlb(input);
  const json = JSON.parse(JSON.stringify(read.json));
  const bin = Buffer.from(read.bin);
  for (const mesh of json.meshes || []) {
    const name = mesh.name || "";
    const mode = /Face|Ponytail/i.test(name) ? "head" : (/BodyMesh/i.test(name) ? "arms" : "");
    if (!mode) continue;
    for (const primitive of mesh.primitives || []) transformPrimitive(json, bin, primitive, pose, mode);
  }
  json.asset = json.asset || { version: "2.0" };
  json.asset.extras = Object.assign({}, json.asset.extras || {}, {
    pose_variant: pose.id,
    pose_label: pose.label,
    pose_note: "Variant generated by transforming vertices of the deployed real MetaHuman/Fab GLB; no proxy mesh.",
  });
  writeGlb(json, bin, output);
}

const input = process.argv[2];
const outputDir = process.argv[3] || path.dirname(input || "");
if (!input) throw new Error("Usage: node create_metahuman_pose_variants.js input.glb outputDir");
fs.mkdirSync(outputDir, { recursive: true });

const variants = [
  { id: "look-left", label: "Look Left", headYaw: 30 },
  { id: "look-right", label: "Look Right", headYaw: -30 },
  { id: "look-up", label: "Look Up", headPitch: -17 },
  { id: "look-down", label: "Look Down", headPitch: 18 },
  { id: "arms-shoulder", label: "Arms Shoulder Height", armsOut: true },
];

const results = [];
for (const pose of variants) {
  const output = path.join(outputDir, `metahuman-current-pose-${pose.id}.glb`);
  createPoseVariant(input, output, pose);
  results.push({ id: pose.id, output, bytes: fs.statSync(output).size });
}
console.log(JSON.stringify({ input, results }, null, 2));
