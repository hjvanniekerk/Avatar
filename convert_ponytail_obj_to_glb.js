const fs = require("fs");
const path = require("path");

function align4(value) {
  return (value + 3) & ~3;
}

function parseArgs(argv) {
  const args = {
    input: argv[2],
    output: argv[3],
    scale: 0.01,
    translate: [0, 1.25, 0],
  };
  for (let i = 4; i < argv.length; i += 1) {
    if (argv[i] === "--scale") args.scale = Number(argv[++i]);
    if (argv[i] === "--translate") args.translate = argv[++i].split(",").map(Number);
  }
  if (!args.input || !args.output) {
    throw new Error("Usage: node convert_ponytail_obj_to_glb.js input.obj output.glb [--scale 0.01] [--translate x,y,z]");
  }
  if (args.translate.length !== 3 || args.translate.some(Number.isNaN) || Number.isNaN(args.scale)) {
    throw new Error("Invalid scale or translate arguments");
  }
  return args;
}

function parseObj(file, scale, translate) {
  const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
  const positions = [];
  const normals = [];
  const texcoords = [];
  const primitiveMap = new Map();
  let group = "default";
  let material = "default";

  function getPrimitive(name) {
    if (!primitiveMap.has(name)) {
      primitiveMap.set(name, {
        name,
        vertexMap: new Map(),
        positions: [],
        normals: [],
        texcoords: [],
        indices: [],
      });
    }
    return primitiveMap.get(name);
  }

  function resolveIndex(value, length) {
    if (!value) return null;
    const index = Number(value);
    if (!Number.isFinite(index) || index === 0) return null;
    return index < 0 ? length + index : index - 1;
  }

  function vertexFor(primitive, token) {
    const [vRaw, vtRaw, vnRaw] = token.split("/");
    const vIndex = resolveIndex(vRaw, positions.length);
    const vtIndex = resolveIndex(vtRaw, texcoords.length);
    const vnIndex = resolveIndex(vnRaw, normals.length);
    if (vIndex == null || !positions[vIndex]) throw new Error(`Bad OBJ vertex token: ${token}`);
    const key = `${vIndex}/${vtIndex ?? ""}/${vnIndex ?? ""}`;
    const existing = primitive.vertexMap.get(key);
    if (existing != null) return existing;

    const pos = positions[vIndex];
    primitive.positions.push(
      pos[0] * scale + translate[0],
      pos[1] * scale + translate[1],
      pos[2] * scale + translate[2],
    );

    const normal = vnIndex == null ? null : normals[vnIndex];
    primitive.normals.push(normal ? normal[0] : 0, normal ? normal[1] : 1, normal ? normal[2] : 0);

    const tex = vtIndex == null ? null : texcoords[vtIndex];
    primitive.texcoords.push(tex ? tex[0] : 0, tex ? 1 - tex[1] : 0);

    const next = primitive.positions.length / 3 - 1;
    primitive.vertexMap.set(key, next);
    return next;
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const parts = trimmed.split(/\s+/);
    if (parts[0] === "v") {
      positions.push(parts.slice(1, 4).map(Number));
    } else if (parts[0] === "vn") {
      normals.push(parts.slice(1, 4).map(Number));
    } else if (parts[0] === "vt") {
      texcoords.push(parts.slice(1, 3).map(Number));
    } else if (parts[0] === "g") {
      group = parts.slice(1).join(" ") || "default";
    } else if (parts[0] === "usemtl") {
      material = parts.slice(1).join(" ") || "default";
    } else if (parts[0] === "f" && (group === "Hair" || group === "Scalp")) {
      const primitive = getPrimitive(`${group}|${material}`);
      const face = parts.slice(1).map((token) => vertexFor(primitive, token));
      for (let i = 1; i + 1 < face.length; i += 1) {
        primitive.indices.push(face[0], face[i], face[i + 1]);
      }
    }
  }

  return Array.from(primitiveMap.values()).filter((primitive) => primitive.indices.length);
}

function bounds(values, size) {
  const min = Array(size).fill(Infinity);
  const max = Array(size).fill(-Infinity);
  for (let i = 0; i < values.length; i += size) {
    for (let k = 0; k < size; k += 1) {
      const value = values[i + k];
      if (value < min[k]) min[k] = value;
      if (value > max[k]) max[k] = value;
    }
  }
  return { min, max };
}

function pushBuffer(parts, buffer) {
  const rawOffset = parts.reduce((sum, part) => sum + part.length, 0);
  const offset = align4(rawOffset);
  if (offset > rawOffset) parts.push(Buffer.alloc(offset - rawOffset));
  parts.push(buffer);
  return offset;
}

function floatBuffer(values) {
  const buffer = Buffer.alloc(values.length * 4);
  for (let i = 0; i < values.length; i += 1) buffer.writeFloatLE(values[i], i * 4);
  return buffer;
}

function uintBuffer(values) {
  const buffer = Buffer.alloc(values.length * 4);
  for (let i = 0; i < values.length; i += 1) buffer.writeUInt32LE(values[i], i * 4);
  return buffer;
}

function buildGlb(primitives, output, extras) {
  const binParts = [];
  const json = {
    asset: {
      version: "2.0",
      generator: "convert_ponytail_obj_to_glb.js",
      extras,
    },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ name: "Fab Realistic Ponytail Root", mesh: 0 }],
    meshes: [{ name: "Fab_Realistic_Long_Black_Ponytail", primitives: [] }],
    materials: [
      {
        name: "Fab_Long_Black_Ponytail_Hair",
        pbrMetallicRoughness: {
          baseColorFactor: [0, 0, 0, 1],
          metallicFactor: 0,
          roughnessFactor: 0.72,
        },
        doubleSided: true,
      },
      {
        name: "Fab_Long_Black_Ponytail_Scalp",
        pbrMetallicRoughness: {
          baseColorFactor: [0.003, 0.003, 0.003, 1],
          metallicFactor: 0,
          roughnessFactor: 0.76,
        },
        doubleSided: true,
      },
    ],
    buffers: [{ byteLength: 0 }],
    bufferViews: [],
    accessors: [],
  };

  for (const primitive of primitives) {
    const materialIndex = primitive.name.startsWith("Scalp|") ? 1 : 0;
    const positionBounds = bounds(primitive.positions, 3);
    const normalBounds = bounds(primitive.normals, 3);
    const texcoordBounds = bounds(primitive.texcoords, 2);
    const indexBounds = bounds(primitive.indices, 1);

    const positionOffset = pushBuffer(binParts, floatBuffer(primitive.positions));
    const normalOffset = pushBuffer(binParts, floatBuffer(primitive.normals));
    const texcoordOffset = pushBuffer(binParts, floatBuffer(primitive.texcoords));
    const indexOffset = pushBuffer(binParts, uintBuffer(primitive.indices));

    const positionView = json.bufferViews.length;
    json.bufferViews.push({ buffer: 0, byteOffset: positionOffset, byteLength: primitive.positions.length * 4, target: 34962 });
    const normalView = json.bufferViews.length;
    json.bufferViews.push({ buffer: 0, byteOffset: normalOffset, byteLength: primitive.normals.length * 4, target: 34962 });
    const texcoordView = json.bufferViews.length;
    json.bufferViews.push({ buffer: 0, byteOffset: texcoordOffset, byteLength: primitive.texcoords.length * 4, target: 34962 });
    const indexView = json.bufferViews.length;
    json.bufferViews.push({ buffer: 0, byteOffset: indexOffset, byteLength: primitive.indices.length * 4, target: 34963 });

    const positionAccessor = json.accessors.length;
    json.accessors.push({
      bufferView: positionView,
      componentType: 5126,
      count: primitive.positions.length / 3,
      type: "VEC3",
      min: positionBounds.min,
      max: positionBounds.max,
    });
    const normalAccessor = json.accessors.length;
    json.accessors.push({
      bufferView: normalView,
      componentType: 5126,
      count: primitive.normals.length / 3,
      type: "VEC3",
      min: normalBounds.min,
      max: normalBounds.max,
    });
    const texcoordAccessor = json.accessors.length;
    json.accessors.push({
      bufferView: texcoordView,
      componentType: 5126,
      count: primitive.texcoords.length / 2,
      type: "VEC2",
      min: texcoordBounds.min,
      max: texcoordBounds.max,
    });
    const indexAccessor = json.accessors.length;
    json.accessors.push({
      bufferView: indexView,
      componentType: 5125,
      count: primitive.indices.length,
      type: "SCALAR",
      min: indexBounds.min,
      max: indexBounds.max,
    });

    json.meshes[0].primitives.push({
      attributes: {
        POSITION: positionAccessor,
        NORMAL: normalAccessor,
        TEXCOORD_0: texcoordAccessor,
      },
      indices: indexAccessor,
      material: materialIndex,
    });
  }

  const rawBinLength = binParts.reduce((sum, part) => sum + part.length, 0);
  const bin = Buffer.concat([...binParts, Buffer.alloc(align4(rawBinLength) - rawBinLength)]);
  json.buffers[0].byteLength = bin.length;

  let jsonBuffer = Buffer.from(JSON.stringify(json), "utf8");
  jsonBuffer = Buffer.concat([jsonBuffer, Buffer.alloc(align4(jsonBuffer.length) - jsonBuffer.length, 0x20)]);

  const header = Buffer.alloc(12);
  header.write("glTF", 0, "ascii");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonBuffer.length + 8 + bin.length, 8);

  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonBuffer.length, 0);
  jsonHeader.write("JSON", 4, "ascii");

  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(bin.length, 0);
  binHeader.write("BIN\0", 4, "ascii");

  fs.writeFileSync(output, Buffer.concat([header, jsonHeader, jsonBuffer, binHeader, bin]));
}

const args = parseArgs(process.argv);
const primitives = parseObj(args.input, args.scale, args.translate);
if (!primitives.length) throw new Error("No Hair or Scalp groups were exported from OBJ");
buildGlb(primitives, args.output, {
  source: path.basename(args.input),
  asset: "Game-Ready 3D Female Hair Realistic Ponytail Model",
  groups: primitives.map((primitive) => ({
    name: primitive.name,
    vertices: primitive.positions.length / 3,
    triangles: primitive.indices.length / 3,
  })),
  transform: { scale: args.scale, translate: args.translate },
});
console.log(JSON.stringify({
  input: args.input,
  output: args.output,
  bytes: fs.statSync(args.output).size,
  primitives: primitives.map((primitive) => ({
    name: primitive.name,
    vertices: primitive.positions.length / 3,
    triangles: primitive.indices.length / 3,
  })),
  transform: { scale: args.scale, translate: args.translate },
}, null, 2));
