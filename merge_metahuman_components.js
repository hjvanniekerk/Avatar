const fs = require("fs");
const path = require("path");

function align4(value){
  return (value + 3) & ~3;
}

function readGlb(file){
  const data = fs.readFileSync(file);
  if (data.toString("ascii", 0, 4) !== "glTF") throw new Error("Not a GLB: " + file);
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
  if (!json || !bin) throw new Error("GLB must contain JSON and BIN chunks: " + file);
  return {json, bin};
}

function unionArray(a, b){
  return Array.from(new Set([...(a || []), ...(b || [])]));
}

function remapPrimitive(primitive, accessorOffset, materialOffset){
  const copy = JSON.parse(JSON.stringify(primitive));
  if (copy.indices != null) copy.indices += accessorOffset;
  if (copy.material != null) copy.material += materialOffset;
  if (copy.attributes){
    for (const key of Object.keys(copy.attributes)) copy.attributes[key] += accessorOffset;
  }
  if (Array.isArray(copy.targets)){
    copy.targets = copy.targets.map(target => {
      const next = {};
      for (const key of Object.keys(target)) next[key] = target[key] + accessorOffset;
      return next;
    });
  }
  return copy;
}

function appendComponent(base, component, name){
  const json = base.json;
  const source = component.json;
  const currentLength = base.binParts.reduce((sum, part) => sum + part.length, 0);
  const aligned = align4(currentLength);
  if (aligned > currentLength) base.binParts.push(Buffer.alloc(aligned - currentLength));
  const binOffset = aligned;
  base.binParts.push(component.bin);

  const bufferViewOffset = json.bufferViews.length;
  const accessorOffset = json.accessors.length;
  const materialOffset = (json.materials || []).length;
  const meshOffset = (json.meshes || []).length;
  const nodeOffset = (json.nodes || []).length;

  json.bufferViews = json.bufferViews || [];
  json.accessors = json.accessors || [];
  json.materials = json.materials || [];
  json.meshes = json.meshes || [];
  json.nodes = json.nodes || [];

  for (const view of source.bufferViews || []){
    const copy = JSON.parse(JSON.stringify(view));
    copy.buffer = 0;
    copy.byteOffset = (copy.byteOffset || 0) + binOffset;
    json.bufferViews.push(copy);
  }
  for (const accessor of source.accessors || []){
    const copy = JSON.parse(JSON.stringify(accessor));
    if (copy.bufferView != null) copy.bufferView += bufferViewOffset;
    json.accessors.push(copy);
  }
  for (const material of source.materials || []){
    const copy = JSON.parse(JSON.stringify(material));
    json.materials.push(copy);
  }
  for (const mesh of source.meshes || []){
    const copy = JSON.parse(JSON.stringify(mesh));
    copy.name = copy.name || name;
    copy.primitives = (copy.primitives || []).map(primitive => remapPrimitive(primitive, accessorOffset, materialOffset));
    json.meshes.push(copy);
  }
  for (const node of source.nodes || []){
    const copy = JSON.parse(JSON.stringify(node));
    if (copy.mesh != null) copy.mesh += meshOffset;
    if (Array.isArray(copy.children)) copy.children = copy.children.map(index => index + nodeOffset);
    copy.name = copy.name || name;
    json.nodes.push(copy);
  }

  const sceneIndex = source.scene || 0;
  const sceneNodes = (source.scenes && source.scenes[sceneIndex] && source.scenes[sceneIndex].nodes) || [];
  const rootNode = json.nodes[0];
  rootNode.children = Array.from(new Set([...(rootNode.children || []), ...sceneNodes.map(index => index + nodeOffset)]));

  json.extensionsUsed = unionArray(json.extensionsUsed, source.extensionsUsed);
  json.extensionsRequired = unionArray(json.extensionsRequired, source.extensionsRequired);
}

function writeGlb(json, binParts, output){
  const binLength = align4(binParts.reduce((sum, part) => sum + part.length, 0));
  const bin = Buffer.concat([...binParts, Buffer.alloc(binLength - binParts.reduce((sum, part) => sum + part.length, 0))]);
  json.buffers = [{byteLength: bin.length}];
  json.asset = json.asset || {version: "2.0"};
  json.asset.extras = Object.assign({}, json.asset.extras || {}, {
    metahuman_component_merge: "BP_Lilly LOD1 body/face with Khaos outfit and hair card meshes",
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

const root = process.cwd();
const basePath = process.argv[2] || path.join(root, "assets", "metahuman-lod1-static-clean.glb");
const output = process.argv[3] || path.join(root, "assets", "metahuman-assembled.glb");
const components = process.argv.slice(4);
if (!components.length){
  components.push(
    path.join(root, "assets", "metahuman-outfit-lod1-clean.glb"),
    path.join(root, "assets", "metahuman-hair-cards-lod1-clean.glb")
  );
}

const baseRead = readGlb(basePath);
const base = {json: baseRead.json, binParts: [baseRead.bin]};
for (const file of components){
  appendComponent(base, readGlb(file), path.basename(file, ".glb"));
}
writeGlb(base.json, base.binParts, output);
console.log(JSON.stringify({base: basePath, components, output, bytes: fs.statSync(output).size}, null, 2));
