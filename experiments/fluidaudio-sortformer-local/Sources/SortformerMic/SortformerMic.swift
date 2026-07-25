@preconcurrency import AVFoundation
import CoreML
import Darwin
import FluidAudio
import Foundation

private struct AudioChunk: Sendable {
    let samples: [Float]
    let sampleRate: Double
}

private final class MicrophoneCapture: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private var continuation: AsyncStream<AudioChunk>.Continuation?
    private var tapInstalled = false

    func start() throws -> (stream: AsyncStream<AudioChunk>, format: AVAudioFormat) {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)

        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw AppError.microphoneUnavailable
        }

        let (stream, continuation) = AsyncStream.makeStream(
            of: AudioChunk.self,
            bufferingPolicy: .bufferingNewest(512)
        )
        self.continuation = continuation

        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            guard let channels = buffer.floatChannelData else { return }

            let frameCount = Int(buffer.frameLength)
            let channelCount = Int(buffer.format.channelCount)
            guard frameCount > 0, channelCount > 0 else { return }

            var mono = [Float](repeating: 0, count: frameCount)
            if channelCount == 1 {
                mono.withUnsafeMutableBufferPointer { destination in
                    destination.baseAddress?.update(from: channels[0], count: frameCount)
                }
            } else {
                let scale = 1.0 / Float(channelCount)
                for channel in 0..<channelCount {
                    let source = channels[channel]
                    for frame in 0..<frameCount {
                        mono[frame] += source[frame] * scale
                    }
                }
            }

            continuation.yield(AudioChunk(samples: mono, sampleRate: buffer.format.sampleRate))
        }
        tapInstalled = true

        engine.prepare()
        do {
            try engine.start()
        } catch {
            stop()
            throw error
        }

        return (stream, format)
    }

    func stop() {
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        engine.stop()
        continuation?.finish()
        continuation = nil
    }

    deinit {
        stop()
    }
}

private enum ModelPreset: String {
    case balanced
    case fast
    case efficient
    case highContext = "high-context"

    var config: SortformerConfig {
        switch self {
        case .balanced:
            return .balancedV2_1
        case .fast:
            return .fastV2_1
        case .efficient:
            return .efficientV2_1
        case .highContext:
            return .highContextV2_1
        }
    }
}

private struct Options {
    enum Command: String {
        case doctor
        case file
        case mic
    }

    var command: Command = .mic
    var duration: TimeInterval = 90
    var inputFile: URL?
    var jsonOutput: URL?
    var preset: ModelPreset = .balanced
    var showTentative = false
    var cacheDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent(".models", isDirectory: true)

    static func parse(_ arguments: [String]) throws -> Options {
        var options = Options()
        var index = 0

        if let first = arguments.first, let command = Command(rawValue: first) {
            options.command = command
            index = 1
        }

        while index < arguments.count {
            let argument = arguments[index]
            switch argument {
            case "--duration":
                index += 1
                guard index < arguments.count,
                      let duration = TimeInterval(arguments[index]),
                      duration > 0
                else {
                    throw AppError.invalidArgument("--duration pozitif bir saniye değeri olmalı")
                }
                options.duration = duration
            case "--input":
                index += 1
                guard index < arguments.count else {
                    throw AppError.invalidArgument("--input için bir WAV yolu gerekli")
                }
                options.inputFile = URL(
                    fileURLWithPath: NSString(string: arguments[index]).expandingTildeInPath
                )
            case "--json":
                index += 1
                guard index < arguments.count else {
                    throw AppError.invalidArgument("--json için bir çıktı yolu gerekli")
                }
                options.jsonOutput = URL(
                    fileURLWithPath: NSString(string: arguments[index]).expandingTildeInPath
                )
            case "--preset":
                index += 1
                guard index < arguments.count,
                      let preset = ModelPreset(rawValue: arguments[index])
                else {
                    throw AppError.invalidArgument(
                        "--preset balanced, fast, efficient veya high-context olmalı"
                    )
                }
                options.preset = preset
            case "--cache":
                index += 1
                guard index < arguments.count else {
                    throw AppError.invalidArgument("--cache için bir klasör yolu gerekli")
                }
                options.cacheDirectory = URL(
                    fileURLWithPath: NSString(string: arguments[index]).expandingTildeInPath,
                    isDirectory: true
                )
            case "--tentative":
                options.showTentative = true
            case "--help", "-h":
                printHelp()
                Darwin.exit(0)
            default:
                throw AppError.invalidArgument("bilinmeyen seçenek: \(argument)")
            }
            index += 1
        }

        return options
    }

    static func printHelp() {
        print(
            """
            FluidAudio Sortformer yerel mikrofon deneyi

            Kullanım:
              swift run -c release SortformerMic doctor
              swift run -c release SortformerMic file --input test.wav [--json sonuc.json]
              swift run -c release SortformerMic mic [seçenekler]

            Seçenekler:
              --duration <saniye>   Kayıt süresi (varsayılan: 90)
              --input <wav>         File komutu için ses dosyası
              --json <dosya>        Segmentleri JSON olarak kaydet
              --preset <ad>         balanced | fast | efficient | high-context
              --cache <klasör>      Model önbelleği (varsayılan: .models)
              --tentative           Kesinleşmemiş canlı parçaları da göster
              -h, --help            Bu yardımı göster
            """
        )
    }
}

private enum AppError: LocalizedError {
    case invalidArgument(String)
    case microphoneUnavailable
    case missingInputFile

    var errorDescription: String? {
        switch self {
        case .invalidArgument(let message):
            return message
        case .microphoneUnavailable:
            return "Kullanılabilir bir mikrofon girişi bulunamadı"
        case .missingInputFile:
            return "file komutu --input ile bir ses dosyası bekliyor"
        }
    }
}

private struct SegmentRecord: Codable {
    let speaker: String
    let start: Double
    let end: Double
    let activity: Double
}

private struct FileDiarizationResult: Codable {
    let engine: String
    let preset: String
    let input: String
    let elapsedSeconds: Double
    let segments: [SegmentRecord]
}

private struct TimelinePrinter {
    private var lastTentative: String?

    mutating func printUpdate(_ update: DiarizerTimelineUpdate, showTentative: Bool) {
        for segment in update.finalizedSegments {
            print(segmentLine(segment, marker: "KESİN"))
        }

        guard showTentative else { return }
        let key = update.tentativeSegments.map {
            "\($0.speakerIndex):\($0.startFrame):\($0.endFrame)"
        }.joined(separator: ",")
        guard !key.isEmpty, key != lastTentative else { return }
        lastTentative = key

        for segment in update.tentativeSegments {
            print(segmentLine(segment, marker: "ANLIK"))
        }
    }

    func printFinalTimeline(_ timeline: DiarizerTimeline) {
        print("\nSon zaman çizelgesi:")
        let segments = timeline.speakers.values
            .flatMap(\.finalizedSegments)
            .sorted()

        if segments.isEmpty {
            print("  Konuşma segmenti bulunamadı.")
            return
        }

        for segment in segments {
            print("  " + segmentLine(segment, marker: nil))
        }
    }

    private func segmentLine(_ segment: DiarizerSegment, marker: String?) -> String {
        let prefix = marker.map { "[\($0)] " } ?? ""
        return String(
            format: "%@[%.2f–%.2f] SPEAKER_%02d  aktivite=%.2f",
            prefix,
            segment.startTime,
            segment.endTime,
            segment.speakerIndex,
            segment.activity
        )
    }
}

@main
private struct SortformerMicApp {
    static func main() async {
        do {
            let options = try Options.parse(Array(CommandLine.arguments.dropFirst()))
            try FileManager.default.createDirectory(
                at: options.cacheDirectory,
                withIntermediateDirectories: true
            )

            print("FluidAudio Sortformer hazırlanıyor")
            print("  sürüm: 0.15.5")
            print("  preset: \(options.preset.rawValue)")
            print("  model önbelleği: \(options.cacheDirectory.path)")

            let config = options.preset.config
            let loadStarted = Date()
            let models = try await SortformerModels.loadFromHuggingFace(
                config: config,
                cacheDirectory: options.cacheDirectory
            )
            let diarizer = SortformerDiarizer(config: config)
            diarizer.initialize(models: models)
            print(String(format: "  model hazır: %.2f sn", Date().timeIntervalSince(loadStarted)))

            if options.command == .doctor {
                print("Doctor başarılı: model yüklendi ve Sortformer başlatıldı.")
                return
            }

            switch options.command {
            case .doctor:
                break
            case .file:
                try runFile(diarizer: diarizer, options: options)
            case .mic:
                try await runMicrophone(diarizer: diarizer, options: options)
            }
        } catch {
            fputs("HATA: \(error.localizedDescription)\n", stderr)
            Darwin.exit(1)
        }
    }

    private static func runFile(
        diarizer: SortformerDiarizer,
        options: Options
    ) throws {
        guard let input = options.inputFile else {
            throw AppError.missingInputFile
        }

        print("Dosya işleniyor: \(input.path)")
        let started = Date()
        let timeline = try diarizer.processComplete(
            audioFileURL: input,
            keepingEnrolledSpeakers: nil,
            finalizeOnCompletion: true,
            progressCallback: nil
        )
        let elapsed = Date().timeIntervalSince(started)
        let printer = TimelinePrinter()
        printer.printFinalTimeline(timeline)
        print(String(format: "İşleme süresi: %.2f sn", elapsed))

        guard let output = options.jsonOutput else { return }
        let records = timeline.speakers.values
            .flatMap(\.finalizedSegments)
            .sorted()
            .map {
                SegmentRecord(
                    speaker: String(format: "SPEAKER_%02d", $0.speakerIndex),
                    start: Double($0.startTime),
                    end: Double($0.endTime),
                    activity: Double($0.activity)
                )
            }
        let result = FileDiarizationResult(
            engine: "FluidAudio Sortformer 0.15.5",
            preset: options.preset.rawValue,
            input: input.path,
            elapsedSeconds: elapsed,
            segments: records
        )
        try FileManager.default.createDirectory(
            at: output.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(result).write(to: output, options: .atomic)
        print("JSON yazıldı: \(output.path)")
    }

    private static func runMicrophone(
        diarizer: SortformerDiarizer,
        options: Options
    ) async throws {
        let capture = MicrophoneCapture()
        let result = try capture.start()
        defer { capture.stop() }

        print(
            String(
                format: "Mikrofon: %.0f Hz, %d kanal",
                result.format.sampleRate,
                result.format.channelCount
            )
        )
        print(String(format: "%.0f saniye dinleniyor. Erken çıkmak için Ctrl+C.\n", options.duration))

        let deadline = Date().addingTimeInterval(options.duration)
        var pending: [Float] = []
        var pendingRate = result.format.sampleRate
        var printer = TimelinePrinter()

        for await chunk in result.stream {
            if Date() >= deadline { break }

            if chunk.sampleRate != pendingRate, !pending.isEmpty {
                if let update = try diarizer.process(
                    samples: pending,
                    sourceSampleRate: pendingRate
                ) {
                    printer.printUpdate(update, showTentative: options.showTentative)
                }
                pending.removeAll(keepingCapacity: true)
            }

            pendingRate = chunk.sampleRate
            pending.append(contentsOf: chunk.samples)

            let processFrames = max(1, Int(pendingRate * 0.25))
            guard pending.count >= processFrames else { continue }

            if let update = try diarizer.process(
                samples: pending,
                sourceSampleRate: pendingRate
            ) {
                printer.printUpdate(update, showTentative: options.showTentative)
            }
            pending.removeAll(keepingCapacity: true)
        }

        if !pending.isEmpty,
           let update = try diarizer.process(samples: pending, sourceSampleRate: pendingRate) {
            printer.printUpdate(update, showTentative: options.showTentative)
        }
        if let update = try diarizer.finalizeSession() {
            printer.printUpdate(update, showTentative: false)
        }

        printer.printFinalTimeline(diarizer.timeline)
    }
}
