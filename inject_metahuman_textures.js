const fs = require("fs");
const path = require("path");

function align4(value){
  return (value + 3) & ~3;
}

function readGlb(file){
  const data = fs.readFileSync(file);
  if (data.toString("ascii", 0, 4) !== "glTF") throw new Error(`Not a GLB: ${file}`);
  let offset = 12;
  let json = null;
  let bin = null;
  while (offset < data.length){
    const length = data.readUInt32LE(offset);
    const type = data.toString("ascii", offset + 4, offset + 8);
    const chunk = data.subarray(offset + 8, offset + 8 + length);
    if (type === "JSON") json = JSON.parse(chunk.toString("utf8").replace(/\0+$/g, "").trimEnd());
    if (type === "BIN\0") bin = Buffer.from(chunk);
    offset += 8 + length;
  }
  if (!json || !bin) throw new Error(`GLB must contain JSON and BIN chunks: ${file}`);
  return {json, bin};
}

function appendBuffer(base, buffer){
  const currentLength = base.binParts.reduce((sum, part) => sum + part.length, 0);
  const aligned = align4(currentLength);
  if (aligned > currentLength) base.binParts.push(Buffer.alloc(aligned - currentLength));
  const byteOffset = aligned;
  base.binParts.push(buffer);
  return byteOffset;
}

function addEmbeddedImage(base, file){
  const absolute = path.resolve(file);
  const buffer = fs.readFileSync(absolute);
  const byteOffset = appendBuffer(base, buffer);
  const json = base.json;
  json.bufferViews = json.bufferViews || [];
  json.images = json.images || [];
  json.textures = json.textures || [];
  const bufferView = json.bufferViews.length;
  json.bufferViews.push({buffer: 0, byteOffset, byteLength: buffer.length});
  const image = json.images.length;
  json.images.push({name: path.basename(file, path.extname(file)), mimeType: "image/png", bufferView});
  const texture = json.textures.length;
  json.textures.push({source: image});
  return texture;
}

function assignTexture(material, textureIndex, normalIndex){
  material.pbrMetallicRoughness = material.pbrMetallicRoughness || {};
  material.pbrMetallicRoughness.baseColorTexture = {index: textureIndex};
  material.pbrMetallicRoughness.metallicFactor = 0;
  material.pbrMetallicRoughness.roughnessFactor = 0.72;
  if (normalIndex != null) material.normalTexture = {index: normalIndex, scale: 0.65};
}

function writeGlb(json, binParts, output){
  const rawLength = binParts.reduce((sum, part) => sum + part.length, 0);
  const bin = Buffer.concat([...binParts, Buffer.alloc(align4(rawLength) - rawLength)]);
  json.buffers = [{byteLength: bin.length}];
  json.asset = json.asset || {version: "2.0"};
  json.asset.extras = Object.assign({}, json.asset.extras || {}, {
    metahuman_texture_binding: "Real Unreal/Khaos baked texture assets embedded into browser GLB",
  });

  let jsonBuffer = Buffer.from(JSON.stringify(json), "utf8");
  jsonBuffer = Buffer.concat([jsonBuffer, Buffer.alloc(align4(jsonBuffer.length) - jsonBuffer.length, 0x20)]);
  const totalLength = 12 + 8 + jsonBuffer.length + 8 + bin.length;
  const header = Buffer.alloc(12);
  header.write("glTF", 0, "ascii");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(totalLength, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonBuffer.length, 0);
  jsonHeader.write("JSON", 4, "ascii");
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(bin.length, 0);
  binHeader.write("BIN\0", 4, "ascii");
  fs.writeFileSync(output, Buffer.concat([header, jsonHeader, jsonBuffer, binHeader, bin]));
}

const input = process.argv[2];
const output = process.argv[3];
const textureRoot = process.argv[4] || path.join(path.dirname(input), "textures");
if (!input || !output){
  throw new Error("Usage: node inject_metahuman_textures.js input.glb output.glb [textureRoot]");
}

const read = readGlb(input);
const base = {json: read.json, binParts: [read.bin]};
const textures = {
  bodyBase: addEmbeddedImage(base, path.join(textureRoot, "body_bc.png")),
  bodyNormal: addEmbeddedImage(base, path.join(textureRoot, "body_n.png")),
  headBase: addEmbeddedImage(base, path.join(textureRoot, "head_lod1_bc.png")),
  headNormal: addEmbeddedImage(base, path.join(textureRoot, "head_lod1_n.png")),
  leftEye: addEmbeddedImage(base, path.join(textureRoot, "eye_l_bc.png")),
  rightEye: addEmbeddedImage(base, path.join(textureRoot, "eye_r_bc.png")),
  teeth: addEmbeddedImage(base, path.join(textureRoot, "teeth_bc.png")),
};

for (const material of base.json.materials || []){
  const name = material.name || "";
  delete material.extensions?.KHR_materials_unlit;
  if (name.includes("MI_Body_Baked")){
    assignTexture(material, textures.bodyBase, textures.bodyNormal);
  } else if (name.includes("MI_Teeth_Baked")){
    assignTexture(material, textures.headBase, textures.headNormal);
    material.pbrMetallicRoughness.roughnessFactor = 0.64;
  } else if (name.includes("MI_EyeL_Baked")){
    assignTexture(material, textures.leftEye, null);
    material.pbrMetallicRoughness.roughnessFactor = 0.18;
  } else if (name.includes("MI_EyeR_Baked")){
    assignTexture(material, textures.rightEye, null);
    material.pbrMetallicRoughness.roughnessFactor = 0.18;
  } else if (name.includes("Face_EyeShell") || name.includes("LacrimalFluid")){
    material.pbrMetallicRoughness = material.pbrMetallicRoughness || {};
    material.pbrMetallicRoughness.baseColorFactor = [0.94, 0.97, 1.0, 0.18];
    material.pbrMetallicRoughness.metallicFactor = 0;
    material.pbrMetallicRoughness.roughnessFactor = 0.06;
    material.alphaMode = "BLEND";
  } else if (name.includes("Ponytail") || name.includes("WorldGridMaterial") || name.includes("Hair")){
    material.name = name.includes("Scalp") ? "Fab_Long_Black_Ponytail_Scalp" : "Fab_Long_Black_Ponytail_Hair";
    material.pbrMetallicRoughness = {
      baseColorFactor: name.includes("Scalp") ? [0.003, 0.003, 0.003, 1] : [0, 0, 0, 1],
      metallicFactor: 0,
      roughnessFactor: name.includes("Scalp") ? 0.76 : 0.72,
    };
    material.doubleSided = true;
  }
}

base.json.extensionsUsed = (base.json.extensionsUsed || []).filter(name => name !== "KHR_materials_unlit");
base.json.extensionsRequired = (base.json.extensionsRequired || []).filter(name => name !== "KHR_materials_unlit");
writeGlb(base.json, base.binParts, output);
console.log(JSON.stringify({input, output, bytes: fs.statSync(output).size, textures}, null, 2));
