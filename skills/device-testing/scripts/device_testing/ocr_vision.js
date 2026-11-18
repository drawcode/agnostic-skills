ObjC.import('Vision'); ObjC.import('Foundation');
function run(argv) {
  var url = $.NSURL.fileURLWithPath(argv[0]);
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0; req.usesLanguageCorrection = false;
  var h = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $.NSDictionary.dictionary);
  var err = Ref();
  var ok = h.performRequestsError($.NSArray.arrayWithObject(req), err);
  if (!ok) return "ERR " + ObjC.unwrap(err[0].localizedDescription);
  var out = [], res = req.results;
  for (var i = 0; i < res.count; i++) {
    var o = res.objectAtIndex(i), b = o.boundingBox;
    out.push({t: ObjC.unwrap(o.topCandidates(1).objectAtIndex(0).string),
      x: b.origin.x, y: b.origin.y, w: b.size.width, h: b.size.height});
  }
  return JSON.stringify(out);
}
