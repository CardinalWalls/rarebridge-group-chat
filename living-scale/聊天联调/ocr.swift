import Foundation
import Vision
import ImageIO
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(src,0,nil) else { fatalError("Cannot read image") }
let req=VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans","en-US"]
req.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage:image).perform([req])
let rows = (req.results ?? []).compactMap { observation -> [String:Any]? in
 guard let text=observation.topCandidates(1).first else { return nil }
 return ["text":text.string,"confidence":text.confidence]
}
let data=try JSONSerialization.data(withJSONObject:rows,options:[.sortedKeys])
print(String(data:data,encoding:.utf8)!)
