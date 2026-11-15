using NovaLib2;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Buffers.Binary;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using MakeRequestParams = NovaLib2.HTMLUtils.MakeRequestParams;
using RequestMethod = NovaLib2.HTMLUtils.MakeRequestParams.RequestMethod;

namespace FilamentManager.Services {
	/// <summary>Client for the SpoolEase console web API (core/src/web_app.rs). Every body and response is AES-256-GCM encrypted with a key derived from the console security key.</summary>
	public class SpoolEaseConsole : RestRequesterBasedService {
		private readonly SpoolEaseCrypto crypto;
		private readonly string apiBaseUrl;

		public SpoolEaseConsole(Enum logCategory, string consoleBaseUrl, string securityKey) {
			ArgumentException.ThrowIfNullOrWhiteSpace(consoleBaseUrl);
			ArgumentNullException.ThrowIfNull(securityKey);
			apiBaseUrl = consoleBaseUrl.TrimEnd('/') + "/api/";
			requester = new JsonRestRequester(logCategory, apiBaseUrl);
			crypto = new SpoolEaseCrypto(securityKey);
		}

		#region Spools

		public async Task<IReadOnlyList<Spool>> GetSpools() => Spool.ParseCsv(await RequestPlaintext(RequestMethod.GET, "spools"));

		public async Task<IReadOnlyDictionary<string, string>> GetSpoolsInPrinters() =>
			(await Get<SpoolsInPrintersResponse>("spools-in-printers")).Spools;

		#region Printer status

		/// <summary>Returns the current connection, print, diagnostic, and slot status for every configured printer.</summary>
		public async Task<IReadOnlyList<PrinterStatus>> GetPrintersStatus() =>
			(await Get<PrintersStatusResponse>("printers-status")).Printers;

		#endregion

		/// <summary>Spool currently staged on the console (last scanned/selected), or null when nothing is staged.</summary>
		public async Task<Spool?> GetSpoolStaging() {
			var response = await Get<SpoolStagingResponse>("spool-staging");
			return Spool.ParseCsv(response.CsvRecord).FirstOrDefault();
		}

		/// <summary>Returns null when the spool doesn't exist or has no stored K calibration.</summary>
		public async Task<KInfo?> GetSpoolKInfo(string spoolId) {
			ArgumentException.ThrowIfNullOrWhiteSpace(spoolId);
			try {
				return (await Post<SpoolKInfoResponse>("spool-kinfo", new { id = spoolId })).KInfo;
			} catch (SpoolEaseApiException ex) when (ex.StatusCode == HttpStatusCode.NotFound) {
				return null;
			}
		}

		/// <summary>Adds the spool when <see cref="Spool.Id"/> is empty, otherwise edits it. Weights/consumption/encode time are console-owned and ignored on edit.</summary>
		public async Task<AddOrEditSpoolResult> AddOrEditSpool(Spool spool, AddOrEditSpoolOptions? options = null) {
			ArgumentNullException.ThrowIfNull(spool);
			options ??= new();
			var kInfo = options.KInfo;
			// The console replaces the stored K info with whatever is sent, so an edit without it would wipe existing calibrations.
			if (kInfo is null && options.PreserveExistingKInfo && !spool.IsNew && spool.HasKInfo)
				kInfo = await GetSpoolKInfo(spool.Id);

			var response = await Post<AddOrEditSpoolResponse>("spools/add-edit", AddOrEditSpoolRequest.From(spool, kInfo, options.SplitFromStockId));
			return new(response.Id, [.. response.ChangedRecords.SelectMany(Spool.ParseCsv)], response.IdsHash);
		}

		public async Task<DeleteSpoolResult> DeleteSpool(string spoolId) {
			ArgumentException.ThrowIfNullOrWhiteSpace(spoolId);
			var response = await Post<DeleteSpoolResponse>("spools/delete", new { id = spoolId });
			return new(response.DeletedIds, response.IdsHash);
		}

		public async Task<string> SetSpoolLocation(string spoolId, string? location, SpoolLocationType locationType) {
			ArgumentException.ThrowIfNullOrWhiteSpace(spoolId);
			return (await Post<GenericResponse>("spool-location", new {
				spool_id = spoolId,
				location = location ?? string.Empty,
				location_type = locationType.ToString()
			})).EnsureSuccess();
		}

		#endregion

		#region Tags

		/// <summary>Returns the location a tag is registered to, or null if the tag isn't a location tag.</summary>
		public async Task<string?> GetTagLocation(string tagIdHex) {
			ArgumentException.ThrowIfNullOrWhiteSpace(tagIdHex);
			var response = await Post<TagScannedResponse>("tag-scanned", new { tag_id_hex = tagIdHex });
			return response.TagInfo is JObject tagInfo ? tagInfo["Location"]?["location"]?.Value<string>() : null;
		}

		/// <summary>Registers a tag as a location tag; an empty location unregisters it.</summary>
		public async Task<string> SetTagLocation(string tagIdHex, string? location) {
			ArgumentException.ThrowIfNullOrWhiteSpace(tagIdHex);
			return (await Post<GenericResponse>("set-tag-location", new { tag_id_hex = tagIdHex, location = location ?? string.Empty })).EnsureSuccess();
		}

		#endregion

		#region Pressure advance

		public async Task<IReadOnlyDictionary<string, PrinterPressureAdvance>> GetPrintersFilamentPressureAdvance(string slicerFilamentCode) {
			ArgumentException.ThrowIfNullOrWhiteSpace(slicerFilamentCode);
			return (await Post<PrintersFilamentPaResponse>("printers-filament-pa", new { slicer_filament_code = slicerFilamentCode })).Printers;
		}

		public async Task<string> AddPrinterPressureAdvance(string printerSerial, string filamentId, PressureAdvanceEntry entry) {
			ArgumentException.ThrowIfNullOrWhiteSpace(printerSerial);
			ArgumentNullException.ThrowIfNull(entry);
			return (await Post<GenericResponse>("add-printer-pa", new { printer_serial = printerSerial, filament_id = filamentId, pressure_advance_entry = entry })).EnsureSuccess();
		}

		#endregion

		#region Transport

		internal static readonly JsonSerializerSettings JsonSettings = new() {
			ContractResolver = new DefaultContractResolver { NamingStrategy = new SnakeCaseNamingStrategy() },
		};

		/// <summary>GET an endpoint and deserialize its decrypted JSON; usable for endpoints without a typed wrapper.</summary>
		public async Task<T> Get<T>(string endpoint) => Deserialize<T>(await RequestPlaintext(RequestMethod.GET, endpoint));

		/// <summary>POST an encrypted JSON body and deserialize the decrypted JSON response; usable for endpoints without a typed wrapper.</summary>
		public async Task<T> Post<T>(string endpoint, object body) => Deserialize<T>(await RequestPlaintext(RequestMethod.POST, endpoint, body));

		private static T Deserialize<T>(string json) =>
			JsonConvert.DeserializeObject<T>(json, JsonSettings) ?? throw new SpoolEaseApiException(HttpStatusCode.OK, $"Console returned an empty {typeof(T).Name}");

		private async Task<string> RequestPlaintext(RequestMethod method, string endpoint, object? body = null) {
			var options = new MakeRequestParams {
				url = apiBaseUrl + endpoint,
				method = method,
				allow_non_success = true,
			};
			if (body is not null) {
				options.body = crypto.Encrypt(JsonConvert.SerializeObject(body, JsonSettings));
				options.content_type_override = "text/plain";
			}

			using var response = await requester.html_utils.MakeRequestRaw(options);
			var text = await response.Content.ReadAsStringAsync();
			if (!response.IsSuccessStatusCode)
				throw new SpoolEaseApiException(response.StatusCode, text);
			// Handlers report failures as unencrypted plain text with a 200, so anything that won't decrypt is an error message.
			if (!crypto.TryDecrypt(text, out var plaintext))
				throw new SpoolEaseApiException(response.StatusCode, string.IsNullOrWhiteSpace(text) ? "Console returned an empty response" : text);
			return plaintext;
		}

		#endregion
	}

	/// <summary>Mirrors framework_web_app/security.rs: PBKDF2-SHA256 key, AES-256-GCM, payload = base64(iv) + base64(ciphertext||tag), both unpadded.</summary>
	internal sealed class SpoolEaseCrypto(string securityKey) {
		private const string Salt = "example_salt";
		private const int Iterations = 10_000;
		private const int NonceSize = 12;
		private const int TagSize = 16;
		private const int NonceBase64Length = 16;

		private readonly byte[] key = Rfc2898DeriveBytes.Pbkdf2(securityKey.Trim(), Encoding.UTF8.GetBytes(Salt), Iterations, HashAlgorithmName.SHA256, 32);

		public string Encrypt(string plaintext) {
			var nonce = RandomNumberGenerator.GetBytes(NonceSize);
			var plainBytes = Encoding.UTF8.GetBytes(plaintext);
			var sealedBytes = new byte[plainBytes.Length + TagSize];
			using var aes = new AesGcm(key, TagSize);
			aes.Encrypt(nonce, plainBytes, sealedBytes.AsSpan(0, plainBytes.Length), sealedBytes.AsSpan(plainBytes.Length));
			return ToBase64NoPad(nonce) + ToBase64NoPad(sealedBytes);
		}

		public bool TryDecrypt(string encrypted, out string plaintext) {
			plaintext = string.Empty;
			if (encrypted.Length < NonceBase64Length)
				return false;
			try {
				var nonce = FromBase64NoPad(encrypted[..NonceBase64Length]);
				var sealedBytes = FromBase64NoPad(encrypted[NonceBase64Length..]);
				if (nonce.Length != NonceSize || sealedBytes.Length < TagSize)
					return false;
				var cipherLength = sealedBytes.Length - TagSize;
				var plainBytes = new byte[cipherLength];
				using var aes = new AesGcm(key, TagSize);
				aes.Decrypt(nonce, sealedBytes.AsSpan(0, cipherLength), sealedBytes.AsSpan(cipherLength), plainBytes);
				plaintext = Encoding.UTF8.GetString(plainBytes);
				return true;
			} catch (Exception ex) when (ex is FormatException or CryptographicException) {
				return false;
			}
		}

		private static string ToBase64NoPad(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=');

		internal static byte[] FromBase64NoPad(string value) => Convert.FromBase64String(value.PadRight(value.Length + (4 - value.Length % 4) % 4, '='));
	}

	public class SpoolEaseApiException(HttpStatusCode statusCode, string message) : Exception(message) {
		public HttpStatusCode StatusCode { get; } = statusCode;
	}

	#region Spool model

	/// <summary>A spool/stock record as stored on the console (SpoolRecord in core/src/spool_record.rs).</summary>
	public class Spool {
		public string Id { get; set; } = string.Empty;
		/// <summary>Primary tag first. Upper-case hex.</summary>
		public IReadOnlyList<string> TagIds { get; set; } = [];
		public string Material { get; set; } = string.Empty;
		public string Subtype { get; set; } = string.Empty;
		public string ColorName { get; set; } = string.Empty;
		/// <summary>RGBA hex codes, more than one for multi-color filaments.</summary>
		public IReadOnlyList<string> ColorCodes { get; set; } = [];
		public string Note { get; set; } = string.Empty;
		public string Brand { get; set; } = string.Empty;
		public int? LabelWeight { get; set; }
		public int? CoreWeight { get; set; }
		/// <summary>Console-owned; ignored on add/edit.</summary>
		public int? WeightNew { get; set; }
		/// <summary>Console-owned; ignored on add/edit.</summary>
		public int? WeightCurrent { get; set; }
		public string SlicerFilament { get; set; } = string.Empty;
		/// <summary>Unix seconds. Only honored when adding.</summary>
		public int? AddedTime { get; set; }
		public int? EncodeTime { get; set; }
		/// <summary>Whether the spool was full/unused when added; null when unknown.</summary>
		public bool? AddedFull { get; set; }
		public float ConsumedSinceAdd { get; set; }
		public float ConsumedSinceWeight { get; set; }
		public bool HasKInfo { get; set; }
		public string DataOrigin { get; set; } = string.Empty;
		public string TagType { get; set; } = string.Empty;
		public string AssignedLocation { get; set; } = string.Empty;
		public string ActualLocation { get; set; } = string.Empty;
		public int SpoolsCount { get; set; } = 1;
		/// <summary>Transmission distance.</summary>
		public float? Td { get; set; }
		/// <summary>Explicit stock flag; null on records written before the flag existed.</summary>
		public bool? Stock { get; set; }

		[JsonIgnore] public bool IsNew => string.IsNullOrEmpty(Id);
		[JsonIgnore] public bool IsStock => Stock ?? SpoolsCount > 1;
		[JsonIgnore] public DateTimeOffset? AddedAt => AddedTime is { } seconds ? DateTimeOffset.FromUnixTimeSeconds(seconds) : null;
		[JsonIgnore] public DateTimeOffset? EncodedAt => EncodeTime is { } seconds ? DateTimeOffset.FromUnixTimeSeconds(seconds) : null;
		/// <summary>Filament left: last weighed gross minus core and use since weighing, else label weight minus use since added.</summary>
		[JsonIgnore] public float? RemainingWeight => WeightCurrent is { } current ? current - (CoreWeight ?? 0) - ConsumedSinceWeight : LabelWeight is { } label ? label - ConsumedSinceAdd : null;
		/// <summary>Identifies spools of the same filament (brand/material/subtype/color).</summary>
		[JsonIgnore] public string FilamentKey => $"{Brand}|{Material}|{Subtype}|{ColorName}".Trim().ToUpperInvariant();

		public override string ToString() => $"{Id,-6} {Brand} {Material} {Subtype} {ColorName} remaining={RemainingWeight:0}g core={CoreWeight}g stock={IsStock} count={SpoolsCount} loc={ActualLocation}";

		/// <summary>Parses headerless console CSV (one record per line, columns in SpoolRecord field order).</summary>
		public static IReadOnlyList<Spool> ParseCsv(string? csv) =>
			string.IsNullOrEmpty(csv) ? [] : [.. SpoolCsv.ParseRows(csv).Select(FromCsvFields).Where(spool => !spool.IsNew)];

		private static Spool FromCsvFields(IReadOnlyList<string> fields) {
			string Field(SpoolCsvColumn column) => (int)column < fields.Count ? fields[(int)column] : string.Empty;
			int? OptionalInt(SpoolCsvColumn column) => int.TryParse(Field(column), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value) ? value : null;

			return new() {
				Id = Field(SpoolCsvColumn.Id),
				TagIds = SpoolCsv.SplitList(Field(SpoolCsvColumn.TagId)),
				Material = Field(SpoolCsvColumn.MaterialType),
				Subtype = Field(SpoolCsvColumn.MaterialSubtype),
				ColorName = Field(SpoolCsvColumn.ColorName),
				ColorCodes = SpoolCsv.SplitList(Field(SpoolCsvColumn.ColorCode)),
				Note = SpoolCsv.DecodeLineSafe(Field(SpoolCsvColumn.Note)),
				Brand = Field(SpoolCsvColumn.Brand),
				LabelWeight = OptionalInt(SpoolCsvColumn.WeightAdvertised),
				CoreWeight = OptionalInt(SpoolCsvColumn.WeightCore),
				WeightNew = OptionalInt(SpoolCsvColumn.WeightNew),
				WeightCurrent = OptionalInt(SpoolCsvColumn.WeightCurrent),
				SlicerFilament = Field(SpoolCsvColumn.SlicerFilament),
				AddedTime = OptionalInt(SpoolCsvColumn.AddedTime),
				EncodeTime = OptionalInt(SpoolCsvColumn.EncodeTime),
				AddedFull = SpoolCsv.ParseYesNo(Field(SpoolCsvColumn.AddedFull)),
				ConsumedSinceAdd = SpoolCsv.ParseBase64Float(Field(SpoolCsvColumn.ConsumedSinceAdd)),
				ConsumedSinceWeight = SpoolCsv.ParseBase64Float(Field(SpoolCsvColumn.ConsumedSinceWeight)),
				HasKInfo = SpoolCsv.ParseYesNo(Field(SpoolCsvColumn.ExtHasK)) ?? false,
				DataOrigin = Field(SpoolCsvColumn.DataOrigin),
				TagType = Field(SpoolCsvColumn.TagType),
				AssignedLocation = Field(SpoolCsvColumn.AssignedLocation),
				ActualLocation = Field(SpoolCsvColumn.ActualLocation),
				SpoolsCount = OptionalInt(SpoolCsvColumn.SpoolsCount) ?? 1,
				Td = float.TryParse(Field(SpoolCsvColumn.Td), NumberStyles.Float, CultureInfo.InvariantCulture, out var td) ? td : null,
				Stock = SpoolCsv.ParseYesNo(Field(SpoolCsvColumn.Stock)),
			};
		}
	}

	/// <summary>Column order of SpoolRecord's serde CSV serialization.</summary>
	internal enum SpoolCsvColumn {
		Id, TagId, MaterialType, MaterialSubtype, ColorName, ColorCode, Note, Brand,
		WeightAdvertised, WeightCore, WeightNew, WeightCurrent, SlicerFilament, AddedTime, EncodeTime, AddedFull,
		ConsumedSinceAdd, ConsumedSinceWeight, ExtHasK, DataOrigin, TagType, AssignedLocation, ActualLocation,
		SpoolsCount, Td, Stock,
	}

	internal static class SpoolCsv {
		/// <summary>RFC 4180 rows as written by serde_csv_core (comma, quote only when needed, doubled quotes, LF terminator).</summary>
		public static IEnumerable<IReadOnlyList<string>> ParseRows(string csv) {
			var row = new List<string>();
			var field = new StringBuilder();
			var inQuotes = false;
			for (var index = 0; index < csv.Length; index++) {
				var character = csv[index];
				if (inQuotes) {
					if (character != '"')
						field.Append(character);
					else if (index + 1 < csv.Length && csv[index + 1] == '"')
						field.Append(csv[++index]);
					else
						inQuotes = false;
					continue;
				}
				switch (character) {
					case '"': inQuotes = true; break;
					case '\r': break;
					case ',':
						row.Add(field.ToString());
						field.Clear();
						break;
					case '\n':
						row.Add(field.ToString());
						field.Clear();
						yield return row;
						row = [];
						break;
					default: field.Append(character); break;
				}
			}
			if (field.Length > 0 || row.Count > 0) {
				row.Add(field.ToString());
				yield return row;
			}
		}

		public static IReadOnlyList<string> SplitList(string value) => value.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

		public static bool? ParseYesNo(string value) => value switch {
			"y" or "Y" => true,
			"n" or "N" => false,
			_ => null,
		};

		/// <summary>Consumption floats are stored as unpadded base64 of the 4 little-endian bytes; empty means 0.</summary>
		public static float ParseBase64Float(string value) {
			if (string.IsNullOrEmpty(value))
				return 0;
			try {
				var bytes = SpoolEaseCrypto.FromBase64NoPad(value);
				return bytes.Length == 4 ? BinaryPrimitives.ReadSingleLittleEndian(bytes) : 0;
			} catch (FormatException) {
				return 0;
			}
		}

		/// <summary>Reverses encode_line_safe_string (core/src/utils.rs): \\n, \\r and \\\\ escapes.</summary>
		public static string DecodeLineSafe(string value) {
			if (!value.Contains('\\'))
				return value;
			var decoded = new StringBuilder(value.Length);
			for (var index = 0; index < value.Length; index++) {
				if (value[index] != '\\' || index + 1 >= value.Length) {
					decoded.Append(value[index]);
					continue;
				}
				var escaped = value[++index];
				decoded.Append(escaped switch {
					'n' => "\n",
					'r' => "\r",
					'\\' => "\\",
					_ => $"\\{escaped}",
				});
			}
			return decoded.ToString();
		}
	}

	public enum SpoolLocationType {
		Assigned,
		Actual
	}

	public record AddOrEditSpoolOptions {
		/// <summary>K calibrations to store with the spool. Sent as-is; replaces what the console has.</summary>
		public KInfo? KInfo { get; set; }
		/// <summary>When editing a spool that has K info and <see cref="KInfo"/> is null, fetch and resend the existing K info so it isn't erased.</summary>
		public bool PreserveExistingKInfo { get; set; } = true;
		/// <summary>Id of a stock record to take <see cref="Spool.SpoolsCount"/> spools out of when adding.</summary>
		public string? SplitFromStockId { get; set; }
	}

	public record AddOrEditSpoolResult(string Id, IReadOnlyList<Spool> ChangedSpools, string IdsHash);

	public record DeleteSpoolResult(IReadOnlyList<string> DeletedIds, string IdsHash);

	#endregion

	#region K info / pressure advance

	public record KInfo {
		public Dictionary<string, KPrinter> Printers { get; set; } = [];
	}

	public record KPrinter {
		public Dictionary<int, KExtruder> Extruders { get; set; } = [];
	}

	public record KExtruder {
		public Dictionary<string, KNozzleDiameter> Diameters { get; set; } = [];
	}

	public record KNozzleDiameter {
		public Dictionary<string, KNozzleId> Nozzles { get; set; } = [];
	}

	public record KNozzleId {
		public string Name { get; set; } = string.Empty;
		public string KValue { get; set; } = string.Empty;
		public int CaliIdx { get; set; }
		public string? SettingId { get; set; }
	}

	public record PrinterPressureAdvance {
		public string Name { get; set; } = string.Empty;
		public uint Extruders { get; set; }
		public List<PressureAdvanceEntry> PressureAdvance { get; set; } = [];
	}


	#region Printer status

	/// <summary>Current status for a configured printer, mirroring <c>PrinterInfo</c> in <c>core/src/view_model.rs</c>.</summary>
	public record PrinterStatus {
		public string PrinterName { get; set; } = string.Empty;
		public string PrinterSerial { get; set; } = string.Empty;
		public bool Connected { get; set; }
		public uint? NumAms { get; set; }
		public PrintState PrintState { get; set; }
		public int? ProgressPercent { get; set; }
		public int? RemainSecs { get; set; }
		public string? PrintName { get; set; }
		public int? Layer { get; set; }
		public int? NumLayers { get; set; }
		public int? Stage { get; set; }
		public int? PrintError { get; set; }
		/// <summary>Each pair contains the printer-reported HMS error attribute and code.</summary>
		public List<int[]> HmsErrors { get; set; } = [];
		public DecodedHMSError[]? DecodedHmsErrors => HmsErrors == null ? [] : [.. HmsErrors.Select(a => new DecodedHMSError(a))];
		public DecodedHMSError? DecodedPrintError => PrintError == null || PrintError == 0 ? null : new DecodedHMSError(PrintError.Value);
		public uint NumExtruders { get; set; }
		public List<PrinterSlotSet> SlotsSets { get; set; } = [];
		public List<PrinterSlotSetDisplayGroup> SlotSetDisplayGroups { get; set; } = [];
	}
	public class DecodedHMSError {
		public DecodedHMSError(params int[] errorParts) {
			// the error parts are two 32 bit integers but the hms errors are 16 bit and in hexidecimal.
			error_code = new short[errorParts.Length * 2];
			for (int i = 0; i < errorParts.Length; i++) {
				error_code[i * 2] = (short)((errorParts[i] >> 16) & 0xFFFF);
				error_code[i * 2 + 1] = (short)(errorParts[i] & 0xFFFF);
			}
		}
		public short[] error_code;
		public string error_string => string.Join("-", error_code.Select(a => a.ToString("X4")));
	}

	public enum PrintState {
		Unknown,
		Idle,
		Prepare,
		Slicing,
		Running,
		Pause,
		Finish,
		Failed,
	}

	public record PrinterSlotSet {
		public string Id { get; set; } = string.Empty;
		public PrinterSlotSetKind Kind { get; set; }
		public string Name { get; set; } = string.Empty;
		public string ShortName { get; set; } = string.Empty;
		/// <summary>Driver-specific slot-group data whose shape varies by printer driver.</summary>
		public JToken? DriverInfo { get; set; }
		public List<string> Slots { get; set; } = [];
		public float? Temp { get; set; }
		public int? Humidity { get; set; }
	}

	public enum PrinterSlotSetKind {
		Ams,
		Ext,
	}

	public record PrinterSlotSetDisplayGroup {
		public string Id { get; set; } = string.Empty;
		public string Name { get; set; } = string.Empty;
		public string ShortName { get; set; } = string.Empty;
		public List<string> SlotSetIds { get; set; } = [];
	}

	#endregion

	public record PressureAdvanceEntry {
		public int Extruder { get; set; }
		public string Diameter { get; set; } = string.Empty;
		public string NozzleId { get; set; } = string.Empty;
		public string Name { get; set; } = string.Empty;
		public string KValue { get; set; } = string.Empty;
		public int CaliIdx { get; set; }
		public string? SettingId { get; set; }
	}

	#endregion

	#region Wire DTOs

	/// <summary>AddSpoolDTO in web_app.rs.</summary>
	internal record AddOrEditSpoolRequest {
		public string TagId { get; set; } = string.Empty;
		public string Id { get; set; } = string.Empty;
		public string Rgba { get; set; } = string.Empty;
		public string ColorName { get; set; } = string.Empty;
		public string Material { get; set; } = string.Empty;
		public string Subtype { get; set; } = string.Empty;
		public string Brand { get; set; } = string.Empty;
		public int CoreWeight { get; set; }
		public int LabelWeight { get; set; }
		public string Note { get; set; } = string.Empty;
		public string SlicerFilament { get; set; } = string.Empty;
		public string FullUnused { get; set; } = string.Empty;
		public KInfo? KInfo { get; set; }
		public string AssignedLocation { get; set; } = string.Empty;
		public string ActualLocation { get; set; } = string.Empty;
		public int SpoolsCount { get; set; } = 1;
		public bool? Stock { get; set; }
		public string? Split { get; set; }
		public int? AddedTime { get; set; }
		public float? Td { get; set; }

		public static AddOrEditSpoolRequest From(Spool spool, KInfo? kInfo, string? splitFromStockId) => new() {
			TagId = string.Join(';', spool.TagIds),
			Id = spool.Id,
			Rgba = string.Join(';', spool.ColorCodes),
			ColorName = spool.ColorName,
			Material = spool.Material,
			Subtype = spool.Subtype,
			Brand = spool.Brand,
			CoreWeight = spool.CoreWeight ?? 0,
			LabelWeight = spool.LabelWeight ?? 0,
			Note = spool.Note,
			SlicerFilament = spool.SlicerFilament,
			FullUnused = spool.AddedFull switch { true => "y", false => "n", null => string.Empty },
			KInfo = kInfo,
			AssignedLocation = spool.AssignedLocation,
			ActualLocation = spool.ActualLocation,
			SpoolsCount = spool.SpoolsCount,
			Stock = spool.Stock,
			Split = splitFromStockId,
			AddedTime = spool.AddedTime,
			Td = spool.Td,
		};
	}

	internal record AddOrEditSpoolResponse {
		public string Id { get; set; } = string.Empty;
		public List<string> ChangedRecords { get; set; } = [];
		public string IdsHash { get; set; } = string.Empty;
	}

	internal record DeleteSpoolResponse {
		public List<string> DeletedIds { get; set; } = [];
		public string IdsHash { get; set; } = string.Empty;
	}

	internal record SpoolsInPrintersResponse {
		public Dictionary<string, string> Spools { get; set; } = [];
	}


	internal record PrintersStatusResponse {
		public List<PrinterStatus> Printers { get; set; } = [];
	}

	internal record SpoolStagingResponse {
		public string CsvRecord { get; set; } = string.Empty;
	}

	internal record SpoolKInfoResponse {
		public KInfo? KInfo { get; set; }
	}

	/// <summary>TagInfo is a serde externally-tagged enum: "Unknown" or {"Location":{"location":"..."}}.</summary>
	internal record TagScannedResponse {
		public JToken? TagInfo { get; set; }
	}

	internal record PrintersFilamentPaResponse {
		public Dictionary<string, PrinterPressureAdvance> Printers { get; set; } = [];
	}

	internal record GenericResponse {
		public string Text { get; set; } = string.Empty;
		public string? Error { get; set; }

		public string EnsureSuccess() => Error is null ? Text : throw new SpoolEaseApiException(HttpStatusCode.OK, Error);
	}

	#endregion
}
